"""
app.py - Site "ResumoPro": login por usuario e senha, envio de PDF e geracao de resumo.
Rodar local:   python app.py      (abre em http://127.0.0.1:5000)
Producao:      gunicorn app:app --workers 1 --threads 4 --timeout 120
"""
import hmac
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import resumo_core

log = logging.getLogger("resumopro")
logging.basicConfig(level=logging.INFO)

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("DATA_DIR", BASE / "data"))
UPLOADS, SAIDA = DATA / "uploads", DATA / "saida"
for p in (DATA, UPLOADS, SAIDA):
    p.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA / "app.db"


def _secret():
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    f = DATA / "secret_key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip()


app = Flask(__name__)
app.config.update(
    SECRET_KEY=_secret(),
    MAX_CONTENT_LENGTH=int(os.environ.get("MAX_UPLOAD_MB", "25")) * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",  # use 1 com HTTPS
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
)
if os.environ.get("BEHIND_PROXY") == "1":  # Render, Railway etc.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# ------------------------------------------------------------------ banco
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  usuario TEXT NOT NULL UNIQUE COLLATE NOCASE,
  senha_hash TEXT NOT NULL,
  admin INTEGER NOT NULL DEFAULT 0,
  criado_em TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  nome TEXT NOT NULL, disciplina TEXT NOT NULL, aula TEXT NOT NULL DEFAULT '',
  modo TEXT NOT NULL, status TEXT NOT NULL,
  progresso INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
  arquivo_saida TEXT, erro TEXT, criado_em TEXT NOT NULL);
"""


def conectar():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def db():
    if "db" not in g:
        g.db = conectar()
    return g.db


@app.teardown_appcontext
def _fechar(_):
    con = g.pop("db", None)
    if con:
        con.close()


def agora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db():
    con = conectar()
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
    for nome, ddl in (("email", "TEXT"), ("tema", "TEXT NOT NULL DEFAULT 'claro'"),
                      ("notif_sistema", "INTEGER NOT NULL DEFAULT 1"),
                      ("notif_lembretes", "INTEGER NOT NULL DEFAULT 1")):
        if nome not in cols:
            con.execute(f"ALTER TABLE users ADD COLUMN {nome} {ddl}")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email COLLATE NOCASE) "
                "WHERE email IS NOT NULL AND email <> ''")
    con.execute("UPDATE jobs SET status='erro', erro='Interrompido (servidor reiniciado).' "
                "WHERE status IN ('fila','processando')")
    n = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if n == 0 and os.environ.get("ADMIN_USER") and os.environ.get("ADMIN_PASSWORD"):
        con.execute("INSERT INTO users(usuario,senha_hash,admin,criado_em) VALUES(?,?,1,?)",
                    (os.environ["ADMIN_USER"].strip(), generate_password_hash(os.environ["ADMIN_PASSWORD"]), agora()))
        log.info("Administrador inicial criado: %s", os.environ["ADMIN_USER"])
    con.commit()
    con.close()


init_db()

# ------------------------------------------------------------------ seguranca
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def _antes():
    g.user = None
    if request.method == "POST":
        enviado = request.form.get("csrf_token", "")
        if not enviado or not hmac.compare_digest(enviado, session.get("csrf", "")):
            abort(400, "Sessão expirada. Recarregue a página e tente novamente.")
    uid = session.get("uid")
    if uid:
        g.user = db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if g.user is None:
            session.clear()


@app.after_request
def _headers(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["X-Frame-Options"] = "DENY"
    r.headers["Referrer-Policy"] = "same-origin"
    if r.mimetype != "application/pdf":
        r.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; "
                                                "style-src 'self'; script-src 'self'; form-action 'self'; frame-ancestors 'none'")
    if g.get("user"):
        r.headers["Cache-Control"] = "no-store"
    return r


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not g.user:
            return redirect(url_for("login"))
        return f(*a, **k)
    return w


def admin_required(f):
    @wraps(f)
    @login_required
    def w(*a, **k):
        if not g.user["admin"]:
            abort(403)
        return f(*a, **k)
    return w


_tentativas = {}  # (ip, usuario) -> [timestamps]
_lock = threading.Lock()


def bloqueado(chave):
    with _lock:
        t = [x for x in _tentativas.get(chave, []) if time.time() - x < 600]
        _tentativas[chave] = t
        return len(t) >= 5


def falhou(chave):
    with _lock:
        _tentativas.setdefault(chave, []).append(time.time())


_HASH_FALSO = generate_password_hash("senha-inexistente")

# ------------------------------------------------------------------ filtros
try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo(os.environ.get("TZ_EXIBICAO", "America/Sao_Paulo"))
except Exception:
    _TZ = timezone(timedelta(hours=-3))


@app.template_filter("dt")
def _dt(s):
    try:
        return datetime.fromisoformat(s).astimezone(_TZ).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return s or ""


@app.template_filter("dia")
def _dia(s):
    try:
        return datetime.fromisoformat(s).astimezone(_TZ).strftime("%d/%m/%Y")
    except Exception:
        return s or ""


def arquivo_nome(j):
    seguro = re.sub(r"[^\w\-]+", "_", j["nome"]).strip("_")[:60] or "resumo"
    return f"Resumo_{seguro}.pdf"


app.jinja_env.globals["arquivo_nome"] = arquivo_nome
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def limpo(s, n):
    return re.sub(r"\s+", " ", (s or "")).strip()[:n]

# ------------------------------------------------------------------ autenticacao
@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("inicio"))
    if request.method == "POST":
        usuario = limpo(request.form.get("usuario"), 60)
        senha = request.form.get("senha", "")
        chave = (request.remote_addr, usuario.lower())
        if bloqueado(chave):
            flash("Muitas tentativas. Aguarde alguns minutos e tente de novo.", "erro")
            return render_template("login.html"), 429
        u = db().execute("SELECT * FROM users WHERE usuario=? OR LOWER(email)=LOWER(?) "
                         "ORDER BY CASE WHEN usuario=? COLLATE NOCASE THEN 0 ELSE 1 END LIMIT 1",
                         (usuario, usuario, usuario)).fetchone()
        ok = check_password_hash(u["senha_hash"] if u else _HASH_FALSO, senha)
        if u and ok:
            session.clear()
            session["uid"] = u["id"]
            session.permanent = True
            return redirect(url_for("inicio"))
        falhou(chave)
        flash("Usuário ou senha incorretos.", "erro")
    return render_template("login.html")


@app.post("/sair")
def sair():
    session.clear()
    return redirect(url_for("login"))

# ------------------------------------------------------------------ painel
@app.get("/")
@login_required
def inicio():
    return render_template("inicio.html", ia_ativa=bool(os.environ.get("ANTHROPIC_API_KEY")),
                           max_mb=app.config["MAX_CONTENT_LENGTH"] // 1024 // 1024)


POR_PAGINA = 8


@app.get("/resumos")
@login_required
def resumos():
    q = limpo(request.args.get("q"), 80)
    st = request.args.get("status", "todos")
    try:
        pg = max(1, int(request.args.get("p", "1")))
    except ValueError:
        pg = 1
    where, params = ["user_id=?"], [g.user["id"]]
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(nome LIKE ? ESCAPE '\\' OR disciplina LIKE ? ESCAPE '\\')")
        params += [like, like]
    filtros = {"concluido": "status='concluido'", "andamento": "status IN ('fila','processando')",
               "erro": "status='erro'"}
    if st in filtros:
        where.append(filtros[st])
    else:
        st = "todos"
    cond = " AND ".join(where)
    total = db().execute(f"SELECT COUNT(*) FROM jobs WHERE {cond}", params).fetchone()[0]
    paginas = max(1, -(-total // POR_PAGINA))
    pg = min(pg, paginas)
    jobs = db().execute(f"SELECT * FROM jobs WHERE {cond} ORDER BY id DESC LIMIT ? OFFSET ?",
                        params + [POR_PAGINA, (pg - 1) * POR_PAGINA]).fetchall()
    return render_template("resumos.html", jobs=jobs, q=q, status=st, pg=pg, paginas=paginas, total=total)


SEM = threading.Semaphore(int(os.environ.get("MAX_JOBS", "2")))


def trabalhador(job_id, pdf_path, disciplina, aula, nome, modo):
    with SEM:
        con = conectar()
        saida = SAIDA / f"{uuid.uuid4().hex}.pdf"
        try:
            con.execute("UPDATE jobs SET status='processando' WHERE id=?", (job_id,))
            con.commit()

            def prog(i, t):
                con.execute("UPDATE jobs SET progresso=?, total=? WHERE id=?", (i, t, job_id))
                con.commit()

            resumo_core.processar(pdf_path, saida, disciplina, aula, nome, modo,
                                  api_key=os.environ.get("ANTHROPIC_API_KEY"),
                                  modelo=os.environ.get("CLAUDE_MODEL"), progresso=prog)
            con.execute("UPDATE jobs SET status='concluido', arquivo_saida=? WHERE id=?", (saida.name, job_id))
        except resumo_core.ErroResumo as e:
            con.execute("UPDATE jobs SET status='erro', erro=? WHERE id=?", (str(e)[:300], job_id))
        except Exception:
            log.exception("Falha no job %s", job_id)
            con.execute("UPDATE jobs SET status='erro', erro=? WHERE id=?",
                        ("Falha ao gerar o resumo. Tente outro arquivo.", job_id))
        finally:
            con.commit()
            con.close()
            try:
                Path(pdf_path).unlink()
            except OSError:
                pass


@app.post("/novo")
@login_required
def novo():
    arq = request.files.get("pdf")
    if not arq or not arq.filename:
        flash("Escolha um arquivo PDF.", "erro")
        return redirect(url_for("inicio"))
    cab = arq.stream.read(5)
    arq.stream.seek(0)
    if not arq.filename.lower().endswith(".pdf") or cab != b"%PDF-":
        flash("O arquivo precisa ser um PDF válido.", "erro")
        return redirect(url_for("inicio"))
    modo = request.form.get("modo", "local")
    if modo not in ("local", "api") or (modo == "api" and not os.environ.get("ANTHROPIC_API_KEY")):
        modo = "local"
    base = Path(arq.filename).stem
    nome = limpo(request.form.get("nome"), 120) or limpo(base.replace("_", " "), 120) or "Resumo"
    disciplina = limpo(request.form.get("disciplina"), 80) or "Resumo de aula"
    aula = limpo(request.form.get("aula"), 20)
    destino = UPLOADS / f"{uuid.uuid4().hex}.pdf"
    arq.save(destino)
    con = db()
    cur = con.execute("INSERT INTO jobs(user_id,nome,disciplina,aula,modo,status,criado_em) VALUES(?,?,?,?,?,?,?)",
                      (g.user["id"], nome, disciplina, aula, modo, "fila", agora()))
    con.commit()
    threading.Thread(target=trabalhador, args=(cur.lastrowid, destino, disciplina, aula, nome, modo), daemon=True).start()
    flash("Arquivo recebido! O resumo está sendo gerado.", "ok")
    return redirect(url_for("resumos"))


@app.get("/api/jobs")
@login_required
def api_jobs():
    rows = db().execute("SELECT id,status,progresso,total FROM jobs WHERE user_id=?", (g.user["id"],)).fetchall()
    return jsonify([dict(r) for r in rows])


def meu_job(job_id):
    j = db().execute("SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, g.user["id"])).fetchone()
    if not j:
        abort(404)
    return j


def arquivo_do_job(j):
    if j["status"] != "concluido" or not j["arquivo_saida"]:
        abort(404)
    arquivo = SAIDA / Path(j["arquivo_saida"]).name
    if not arquivo.exists():
        abort(404)
    return arquivo


@app.get("/download/<int:job_id>")
@login_required
def baixar(job_id):
    j = meu_job(job_id)
    return send_file(arquivo_do_job(j), as_attachment=True, download_name=arquivo_nome(j), mimetype="application/pdf")


@app.get("/ver/<int:job_id>")
@login_required
def ver(job_id):
    j = meu_job(job_id)
    return send_file(arquivo_do_job(j), as_attachment=False, download_name=arquivo_nome(j), mimetype="application/pdf")


def apagar_arquivo(j):
    if j["arquivo_saida"]:
        try:
            (SAIDA / Path(j["arquivo_saida"]).name).unlink()
        except OSError:
            pass


@app.post("/job/<int:job_id>/excluir")
@login_required
def excluir_job(job_id):
    j = meu_job(job_id)
    apagar_arquivo(j)
    db().execute("DELETE FROM jobs WHERE id=?", (job_id,))
    db().commit()
    flash("Resumo excluído.", "ok")
    return redirect(url_for("resumos"))

# ------------------------------------------------------------------ configuracoes e conta
@app.get("/configuracoes")
@login_required
def configuracoes():
    return render_template("configuracoes.html")


@app.post("/configuracoes/tema")
@login_required
def config_tema():
    tema = request.form.get("tema")
    if tema in ("claro", "escuro"):
        db().execute("UPDATE users SET tema=? WHERE id=?", (tema, g.user["id"]))
        db().commit()
    destino = request.form.get("next")
    return redirect(destino if destino in ("/", "/resumos", "/configuracoes") else url_for("configuracoes"))


@app.post("/configuracoes/notificacoes")
@login_required
def config_notif():
    db().execute("UPDATE users SET notif_sistema=?, notif_lembretes=? WHERE id=?",
                 (1 if request.form.get("sistema") else 0, 1 if request.form.get("lembretes") else 0, g.user["id"]))
    db().commit()
    flash("Preferências salvas.", "ok")
    return redirect(url_for("configuracoes"))


@app.route("/conta/perfil", methods=["GET", "POST"])
@login_required
def conta_perfil():
    if request.method == "POST":
        email = limpo(request.form.get("email"), 120).lower() or None
        if email and not EMAIL_RE.match(email):
            flash("E-mail inválido.", "erro")
        else:
            try:
                db().execute("UPDATE users SET email=? WHERE id=?", (email, g.user["id"]))
                db().commit()
                flash("Perfil atualizado.", "ok")
                return redirect(url_for("configuracoes"))
            except sqlite3.IntegrityError:
                flash("Esse e-mail já está em uso.", "erro")
    return render_template("conta_perfil.html")


@app.route("/conta/senha", methods=["GET", "POST"])
@login_required
def conta_senha():
    if request.method == "POST":
        atual, nova, conf = (request.form.get(k, "") for k in ("atual", "nova", "conf"))
        if not check_password_hash(g.user["senha_hash"], atual):
            flash("A senha atual está incorreta.", "erro")
        elif not senha_valida(nova):
            flash("A nova senha precisa ter pelo menos 8 caracteres.", "erro")
        elif nova != conf:
            flash("A confirmação não confere com a nova senha.", "erro")
        else:
            db().execute("UPDATE users SET senha_hash=? WHERE id=?", (generate_password_hash(nova), g.user["id"]))
            db().commit()
            flash("Senha alterada com sucesso.", "ok")
            return redirect(url_for("configuracoes"))
    return render_template("conta_senha.html")


@app.route("/conta/excluir", methods=["GET", "POST"])
@login_required
def conta_excluir():
    if request.method == "POST":
        admins = db().execute("SELECT COUNT(*) FROM users WHERE admin=1").fetchone()[0]
        if not check_password_hash(g.user["senha_hash"], request.form.get("senha", "")):
            flash("Senha incorreta.", "erro")
        elif g.user["admin"] and admins <= 1:
            flash("Você é o único administrador. Crie outro administrador antes de excluir esta conta.", "erro")
        else:
            for j in db().execute("SELECT * FROM jobs WHERE user_id=?", (g.user["id"],)).fetchall():
                apagar_arquivo(j)
            db().execute("DELETE FROM users WHERE id=?", (g.user["id"],))
            db().commit()
            session.clear()
            flash("Sua conta e seus resumos foram excluídos.", "ok")
            return redirect(url_for("login"))
    return render_template("conta_excluir.html")


@app.post("/conta/exportar")
@login_required
def conta_exportar():
    import io, json, zipfile
    jobs = db().execute("SELECT * FROM jobs WHERE user_id=? ORDER BY id", (g.user["id"],)).fetchall()
    dados = {"usuario": g.user["usuario"], "email": g.user["email"], "criado_em": g.user["criado_em"],
             "resumos": [{k: j[k] for k in ("id", "nome", "disciplina", "aula", "modo", "status", "total", "criado_em")}
                         for j in jobs]}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("meus_dados.json", json.dumps(dados, ensure_ascii=False, indent=2))
        for j in jobs:
            if j["status"] == "concluido" and j["arquivo_saida"]:
                f = SAIDA / Path(j["arquivo_saida"]).name
                if f.exists():
                    z.write(f, f"resumos/{j['id']}_{arquivo_nome(j)}")
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="meus_dados_resumopro.zip", mimetype="application/zip")


# ------------------------------------------------------------------ administracao
def senha_valida(s):
    return len(s) >= 8


@app.get("/admin")
@admin_required
def admin():
    us = db().execute("""SELECT u.*, (SELECT COUNT(*) FROM jobs j WHERE j.user_id=u.id) AS n_jobs
                         FROM users u ORDER BY u.id""").fetchall()
    return render_template("admin.html", usuarios=us)


@app.post("/admin/usuarios")
@admin_required
def admin_criar():
    usuario = limpo(request.form.get("usuario"), 60)
    senha = request.form.get("senha", "")
    email = limpo(request.form.get("email"), 120).lower() or None
    if email and not EMAIL_RE.match(email):
        flash("E-mail inválido.", "erro")
    elif not re.fullmatch(r"[\w.@-]{3,60}", usuario):
        flash("Usuário: 3 a 60 caracteres (letras, números, ponto, hífen, @ ou _).", "erro")
    elif not senha_valida(senha):
        flash("A senha precisa ter pelo menos 8 caracteres.", "erro")
    else:
        try:
            db().execute("INSERT INTO users(usuario,senha_hash,admin,criado_em,email) VALUES(?,?,?,?,?)",
                         (usuario, generate_password_hash(senha), 1 if request.form.get("admin") else 0, agora(), email))
            db().commit()
            flash(f"Usuário {usuario} criado.", "ok")
        except sqlite3.IntegrityError:
            flash("Esse usuário ou e-mail já existe.", "erro")
    return redirect(url_for("admin"))


@app.post("/admin/usuarios/<int:uid>/senha")
@admin_required
def admin_senha(uid):
    senha = request.form.get("senha", "")
    if not senha_valida(senha):
        flash("A senha precisa ter pelo menos 8 caracteres.", "erro")
    else:
        db().execute("UPDATE users SET senha_hash=? WHERE id=?", (generate_password_hash(senha), uid))
        db().commit()
        flash("Senha alterada.", "ok")
    return redirect(url_for("admin"))


@app.post("/admin/usuarios/<int:uid>/excluir")
@admin_required
def admin_excluir(uid):
    if uid == g.user["id"]:
        flash("Você não pode excluir a própria conta.", "erro")
        return redirect(url_for("admin"))
    for j in db().execute("SELECT * FROM jobs WHERE user_id=?", (uid,)).fetchall():
        apagar_arquivo(j)
    db().execute("DELETE FROM users WHERE id=?", (uid,))
    db().commit()
    flash("Usuário excluído.", "ok")
    return redirect(url_for("admin"))


@app.cli.command("criar-usuario")
def cli_criar():
    """Cria usuario pelo terminal:  flask --app app criar-usuario"""
    import getpass
    u = input("Usuário: ").strip()
    s = getpass.getpass("Senha (mín. 8): ")
    adm = input("Administrador? [s/N] ").lower().startswith("s")
    con = conectar()
    con.execute("INSERT INTO users(usuario,senha_hash,admin,criado_em) VALUES(?,?,?,?)",
                (u, generate_password_hash(s), int(adm), agora()))
    con.commit()
    print("Criado.")


@app.errorhandler(413)
def _grande(_):
    flash(f"Arquivo muito grande (máx. {app.config['MAX_CONTENT_LENGTH'] // 1024 // 1024} MB).", "erro")
    return redirect(url_for("inicio") if g.get("user") else url_for("login"))


@app.errorhandler(404)
def _404(_):
    return render_template("erro.html", codigo=404, msg="Página não encontrada."), 404


@app.errorhandler(403)
def _403(_):
    return render_template("erro.html", codigo=403, msg="Acesso negado."), 403


@app.errorhandler(400)
def _400(e):
    return render_template("erro.html", codigo=400, msg=getattr(e, "description", "Requisição inválida.")), 400


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
