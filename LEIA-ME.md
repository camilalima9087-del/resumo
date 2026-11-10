# ResumoPro — versão 2 (novo visual verde)

Site com login por usuário e senha. Cada pessoa envia um PDF e baixa o resumo página a página.
O motor de resumo é o seu `resumo_pdf.py` (agora em `resumo_core.py`).

## Estrutura
- `app.py` — servidor (login, usuários, fila de resumos, download)
- `resumo_core.py` — seu código de resumo (modo local e modo IA)
- `templates/` e `static/` — telas, CSS e JavaScript
- `requirements.txt`, `Procfile`, `.env.example` — para hospedar

## Rodar no seu computador (Windows / PowerShell)
```
python -m pip install -r requirements.txt
$env:ADMIN_USER="camila"
$env:ADMIN_PASSWORD="uma-senha-forte-aqui"
python app.py
```
Abra http://127.0.0.1:5000 e entre com o usuário e senha acima (o administrador é criado
na primeira execução, só se ainda não existir nenhum usuário).
Em "Usuários" você cria as contas das outras pessoas.

Para ativar o resumo com IA, defina também `$env:ANTHROPIC_API_KEY="sua_chave"` (gera custo por uso).

## Hospedar (exemplo: Render)
1. Suba esta pasta para um repositório no GitHub (o `.gitignore` já exclui `data/` e `.env`).
2. No Render: New > Web Service > escolha o repositório.
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn app:app --workers 1 --threads 4 --timeout 120`
3. Em Environment, cadastre as variáveis de `.env.example` (SECRET_KEY, ADMIN_USER, ADMIN_PASSWORD,
   COOKIE_SECURE=1, BEHIND_PROXY=1 e, se quiser IA, ANTHROPIC_API_KEY).
4. **Disco persistente:** sem ele, usuários e PDFs somem a cada reinício. Adicione um Disk
   (ex.: montado em `/var/data`) e defina `DATA_DIR=/var/data`. Disco persistente é plano pago.
   Alternativa: PythonAnywhere, onde os arquivos ficam salvos.
5. Use sempre HTTPS (o Render já fornece) e troque a senha do admin criada no início.

Mantenha `--workers 1`: os resumos rodam em segundo plano dentro do próprio processo.

## O que já está protegido
Senhas com hash, proteção contra tentativas repetidas de login (5 por 10 min), CSRF nos formulários,
cada usuário só acessa os próprios resumos, só PDF válido até 25 MB, PDF original apagado após o uso,
cabeçalhos de segurança, contas criadas apenas pelo administrador.

## Limitações desta primeira versão
- Banco SQLite e fila em memória: bom para um grupo pequeno, não para muitos acessos simultâneos.
- Sem "esqueci minha senha": o administrador troca a senha na tela Usuários.
- Sem limite de uso por pessoa no modo IA (cada resumo gasta créditos da sua chave).
- Slides só com imagem só são lidos se o servidor tiver OCR (Tesseract); no padrão aparecem como "sem texto".
- Resumos antigos não são apagados automaticamente.
- O modo IA não foi testado aqui (sem internet no ambiente de teste); o restante foi testado.

## Novidades da versão 2
- Visual novo (verde), com menu lateral: Início, Meus Resumos, Configurações, Usuários (só admin) e Sair.
- Tela de login dividida, com "Criadora: Camila Lima." (edite em `templates/login.html`).
- Início: área de arrastar PDF, botão "Resumir PDF" e "Opções do resumo" (disciplina, aula, nome, tipo).
- Meus Resumos: busca, filtro (Todos, Prontos, Em andamento, Com erro), Visualizar, Baixar, menu ⋮ (Excluir) e paginação (8 por página).
- Configurações: editar e-mail, tema claro/escuro (salvo por usuário), notificações, exportar dados (ZIP), alterar senha e excluir a própria conta.
- Dá para entrar com usuário **ou** e-mail (o e-mail é opcional e é cadastrado em Editar perfil).

## Observações sobre a versão 2
- "Notificações do sistema": avisa pelo navegador quando o resumo fica pronto (o navegador pede permissão).
- "Lembretes de uso": por enquanto só salva a preferência; ainda não envia lembretes.
- O logo é um desenho simplificado em SVG. Para usar o logo exato dos seus modelos, troque o conteúdo da macro `logo` em `templates/_icons.html` por uma imagem sua.
- Os textos de marketing da tela inicial ("em segundos" etc.) estão em `templates/inicio.html`.
- Banco existente da v1: as novas colunas são criadas automaticamente na primeira execução.
