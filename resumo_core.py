"""
resumo_core.py - Motor de resumo (extracao de texto, resumo local/IA e geracao do PDF).
Reaproveitado do script resumo_pdf.py original, sem a parte de linha de comando.
"""
import json, os, re
from collections import Counter

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (BaseDocTemplate, Frame, HRFlowable, KeepTogether, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)
from xml.sax.saxutils import escape

# ---------------------------------------------------------------- extracao
def extrair_paginas(caminho, usar_ocr=True):
    textos = []
    try:
        import pdfplumber
        with pdfplumber.open(caminho) as pdf:
            for p in pdf.pages:
                textos.append((p.extract_text() or "").strip())
    except ImportError:
        from pypdf import PdfReader
        textos = [(p.extract_text() or "").strip() for p in PdfReader(caminho).pages]
    if usar_ocr and any(len(t) < 15 for t in textos):
        try:
            import pytesseract
            from pdf2image import convert_from_path
            vazias = [i for i, t in enumerate(textos) if len(t) < 15]
            for i in vazias:
                img = convert_from_path(caminho, dpi=150, first_page=i + 1, last_page=i + 1)[0]
                textos[i] = pytesseract.image_to_string(img, lang="por").strip()
        except Exception:
            pass  # OCR indisponivel: segue sem ele
    return textos

# ---------------------------------------------------------------- modo local
STOP = set("a o as os de da do das dos e em um uma para por com que se no na nos nas ao aos ou como mais mas "
           "sua seu suas seus ser sao foi ha ja nao sem sobre entre ate quando onde pela pelo pelas pelos "
           "isso este esta esse essa estes estas tambem so muito ela ele eles elas lhe ainda ser deve podem pode".split())
RE_LEI = re.compile(r"(Arts?\.?\s*\d+[\.\dº°-]*(?:,?\s*(?:§\s*\d+º?|[IVX]+|parágrafo único))*[^.;\n]{0,25}(?:CC|CPC|CF|CDC|LINDB|CP|CLT|Lei\s*[\d\./]+)?)"
                    r"|(Súmula\s*(?:n[ºo.]?\s*)?\d+(?:/[A-Z]+)?(?:\s*d[oa]\s*[A-Z]+)?)"
                    r"|(Enunciado\s*\d+)|(Lei\s*(?:n[ºo.]?\s*)?[\d\.]+/\d{4})", re.I)

def sentencas(texto):
    t = re.sub(r"\s*\n\s*", " ", texto)
    return [s.strip() for s in re.split(r"(?<=[\.\!\?;:])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9])", t) if len(s.strip()) > 25]

def resumo_local(texto, n=3):
    linhas = [l.strip() for l in texto.splitlines() if l.strip()]
    titulo = linhas[0][:90] if linhas else "(sem texto)"
    sents = sentencas(texto)
    if not sents:
        return dict(titulo=titulo, resumo=[texto[:400]] if texto else [], conceitos=[], exemplos=[], leis=[])
    freq = Counter(w for w in re.findall(r"\w{4,}", texto.lower()) if w not in STOP)
    pont = [(sum(freq[w] for w in re.findall(r"\w{4,}", s.lower())) / (len(s) ** 0.5), i, s) for i, s in enumerate(sents)]
    top = sorted(sorted(pont, reverse=True)[:n], key=lambda x: x[1])
    leis = []
    for m in RE_LEI.finditer(texto):
        x = re.sub(r"\s+", " ", m.group(0)).strip(" ,.;")
        if x not in leis:
            leis.append(x)
    exemplos = [s for s in sents if re.match(r"(ex\.?|exemplo)\b", s, re.I)][:2]
    return dict(titulo=titulo, resumo=[s for _, _, s in top], conceitos=[], exemplos=exemplos, leis=leis[:6])

# ---------------------------------------------------------------- modo API
PROMPT = """Voce resume slides de aula para um estudante. Resuma o slide abaixo em portugues, de forma clara e fiel ao texto.
Responda APENAS com JSON valido, sem markdown, neste formato:
{"titulo": "...", "resumo": ["paragrafo curto", ...], "conceitos": ["definicao em destaque", ...],
 "exemplos": ["..."], "leis": ["Art. X do CC: o que diz, em uma frase"]}
Use listas vazias quando nao houver. Nao invente nada que nao esteja no texto; se o slide for so um aviso, diga isso no resumo.

SLIDE {n}:
{texto}"""

def chamar(client, modelo, prompt, max_tokens=1200):
    r = client.messages.create(model=modelo, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")

def resumo_api(client, modelo, n, texto):
    if len(texto) < 15:
        return dict(titulo="Slide sem texto extraivel", resumo=["Slide com imagem ou sem texto; nao foi possivel resumir."],
                    conceitos=[], exemplos=[], leis=[])
    bruto = chamar(client, modelo, PROMPT.replace("{n}", str(n)).replace("{texto}", texto[:6000]))
    bruto = re.sub(r"^```(?:json)?|```$", "", bruto.strip(), flags=re.M).strip()
    try:
        d = json.loads(bruto)
        for k in ("resumo", "conceitos", "exemplos", "leis"):
            if isinstance(d.get(k), str): d[k] = [d[k]]
            d.setdefault(k, [])
        d.setdefault("titulo", f"Slide {n}")
        return d
    except Exception:
        return dict(titulo=f"Slide {n}", resumo=[bruto[:600]], conceitos=[], exemplos=[], leis=[])

def revisao_api(client, modelo, resumos):
    base = "\n".join(f"- {d['titulo']}: {' '.join(d['resumo'])[:300]}" for d in resumos)[:12000]
    p = ("Com base nos resumos de slides abaixo, liste de 6 a 12 pontos mais importantes para revisao final, em portugues, "
         "um por linha, sem numeracao e sem markdown.\n\n" + base)
    return [l.strip("-• ").strip() for l in chamar(client, modelo, p, 1500).splitlines() if l.strip()]

def revisao_local(resumos):
    return [f"<b>{d['titulo']}</b>: {d['resumo'][0]}" if d['resumo'] else d['titulo'] for d in resumos if d['resumo']][:12]

# ---------------------------------------------------------------- PDF
H, HB, HI = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"
CINZA = colors.Color(0.92, 0.92, 0.92)
ST = dict(
    cd=ParagraphStyle("cd", fontName=HB, fontSize=14, alignment=TA_CENTER, leading=18),
    ca=ParagraphStyle("ca", fontName=HB, fontSize=26, alignment=TA_CENTER, leading=32),
    cn=ParagraphStyle("cn", fontName=HB, fontSize=18, alignment=TA_CENTER, leading=24),
    sl=ParagraphStyle("sl", fontName=HB, fontSize=12.5, leading=16, spaceBefore=12, spaceAfter=4),
    b=ParagraphStyle("b", fontName=H, fontSize=10.5, leading=14.5, alignment=TA_JUSTIFY, spaceAfter=4),
    bx=ParagraphStyle("bx", fontName=H, fontSize=10, leading=13.5, alignment=TA_JUSTIFY),
    h1=ParagraphStyle("h1", fontName=HB, fontSize=16, leading=20, spaceAfter=8),
    bu=ParagraphStyle("bu", fontName=H, fontSize=10.5, leading=14.5, leftIndent=14, bulletIndent=2, spaceAfter=3, alignment=TA_JUSTIFY),
)

def esc(t):  # permite so <b> vindo de nos; escapa o resto
    return escape(str(t)).replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")

def caixa(rotulo, itens):
    t = Table([[Paragraph(f"<b>{rotulo}:</b> {esc(i)}", ST["bx"])] for i in itens], colWidths=[16.6 * cm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CINZA), ("LINEBEFORE", (0, 0), (0, -1), 2.5, colors.black),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                           ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return [t, Spacer(1, 3)]

def gerar_pdf(saida, disciplina, aula, nome, resumos, revisao):
    rod = f"{disciplina} | {('Aula ' + aula + ' - ') if aula else ''}{nome}"
    def rodape(c, d):
        c.saveState(); c.setFont(H, 8.5)
        c.drawString(2 * cm, 1.2 * cm, rod[:95]); c.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Página {d.page}")
        c.restoreState()
    doc = BaseDocTemplate(saida, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm,
                          bottomMargin=2 * cm, title=f"Resumo - {nome}")
    doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height)], onPage=rodape)])
    s = [Spacer(1, 6 * cm), Paragraph(esc(f"DISCIPLINA: {disciplina.upper()}"), ST["cd"]), Spacer(1, 1 * cm),
         HRFlowable(width="60%", thickness=1.2, color=colors.black, hAlign="CENTER"), Spacer(1, 1 * cm)]
    if aula: s += [Paragraph(f"AULA {esc(aula)}", ST["ca"]), Spacer(1, 0.5 * cm)]
    s += [Paragraph(esc(nome), ST["cn"]), Spacer(1, 1 * cm), HRFlowable(width="60%", thickness=1.2, color=colors.black, hAlign="CENTER"),
          PageBreak(), Paragraph("Resumo slide a slide", ST["h1"])]
    for n, d in enumerate(resumos, 1):
        topo = [Paragraph(f"Slide {n} - {esc(d['titulo'])}", ST["sl"])]
        corpo = [Paragraph(esc(p), ST["b"]) for p in d["resumo"]]
        extra = []
        if d["conceitos"]: extra += caixa("Conceito", d["conceitos"])
        if d["exemplos"]: extra += caixa("Exemplo", d["exemplos"])
        if d["leis"]: extra += caixa("Base legal", d["leis"])
        s += [KeepTogether(topo + corpo[:1])] + corpo[1:] + extra
    if revisao:
        s += [PageBreak(), Paragraph("Revisão final - pontos mais importantes", ST["h1"])]
        s += [Paragraph(esc(r), ST["bu"], bulletText="•") for r in revisao]
    doc.build(s)

# ---------------------------------------------------------------- orquestracao
class ErroResumo(Exception):
    """Erro com mensagem segura para mostrar ao usuario."""

MAX_PAGINAS = int(os.environ.get("MAX_PAGINAS", "150"))

def processar(pdf_path, saida, disciplina, aula, nome, modo="local",
              api_key=None, modelo=None, progresso=None):
    """Le o PDF, resume pagina a pagina e grava o PDF de resumo em `saida`. Retorna o n. de paginas."""
    try:
        paginas = extrair_paginas(str(pdf_path), usar_ocr=True)
    except Exception as e:
        raise ErroResumo("Nao foi possivel ler o PDF (arquivo corrompido ou protegido por senha).") from e
    if not paginas:
        raise ErroResumo("O PDF nao tem paginas.")
    if len(paginas) > MAX_PAGINAS:
        raise ErroResumo(f"O PDF tem {len(paginas)} paginas; o limite e {MAX_PAGINAS}.")
    total = len(paginas)
    if progresso: progresso(0, total)
    if modo == "api":
        if not api_key:
            raise ErroResumo("O resumo com IA nao esta configurado neste servidor.")
        import anthropic
        cli = anthropic.Anthropic(api_key=api_key)
        modelo = modelo or "claude-sonnet-4-6"
        resumos = []
        for i, t in enumerate(paginas, 1):
            resumos.append(resumo_api(cli, modelo, i, t))
            if progresso: progresso(i, total)
        rev = revisao_api(cli, modelo, resumos)
    else:
        resumos = []
        for i, t in enumerate(paginas, 1):
            resumos.append(resumo_local(t))
            if progresso: progresso(i, total)
        rev = revisao_local(resumos)
    gerar_pdf(str(saida), disciplina, aula, nome, resumos, rev)
    return total
