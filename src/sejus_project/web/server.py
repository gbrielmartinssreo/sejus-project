"""Servidor web local do chat SEJUS (interface em vez da CLI).

Endpoint principal: ``POST /api/chat`` chama ``agent.executar`` e devolve a
resposta em markdown junto com a ultima minuta gerada (renderizada em HTML),
quando houver. O upload de arquivos alimenta a pasta ``importacoes_usuario/``
usada pela tool ``analisar_arquivo_usuario``. Qualquer exceção não tratada
vira uma ``JSONResponse`` (nunca um 500 em HTML) para a interface conseguir
interpretar o erro.

Rodar local:
    uv run uvicorn sejus_project.web.server:app --reload
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sejus_project.agent import agent
from sejus_project.tools import document_generation as generation
from sejus_project.tools.pdf_preview import docx_para_pdf, soffice_disponivel
from sejus_project.tools.user_files import IMPORTACOES_DIR, SUPPORTED_EXTENSIONS
from sejus_project.web.render_html import minuta_para_texto

STATIC_DIR = Path(__file__).parent / "static"

# Teto para a mensagem vinda do navegador antes de chegar às tools.
MAX_MESSAGE_CHARS = 80_000

app = FastAPI(title="SEJUS Chat", docs_url="/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ChatMessage(BaseModel):
    message: str


@app.exception_handler(Exception)
async def _erro_interno(_request: Request, exc: Exception) -> JSONResponse:
    """Garante resposta JSON para qualquer erro inesperado (nunca HTML 500)."""
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Erro interno no servidor.",
            "error": str(exc),
        },
    )


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _resposta_chat(reply: str) -> dict:
    minuta_texto = None
    minuta_pdf = None
    minuta_docx = None
    minuta_nome = None
    if generation.consumir_geracao_do_turno():
        ultima = generation.ultima_minuta()
        if ultima:
            output = ultima.get("output_path")
            if output and Path(output).is_file():
                minuta_nome = Path(output).name
                minuta_docx = "/api/minuta/docx"
                if soffice_disponivel():
                    minuta_pdf = "/api/minuta/pdf"
                minuta_texto = minuta_para_texto(ultima["estructura"])

    return {
        "reply": reply,
        "minuta_nome": minuta_nome,
        "minuta_texto": minuta_texto,
        "minuta_pdf": minuta_pdf,
        "minuta_docx": minuta_docx,
        "pendente": generation.has_pending_document(),
        "campos": generation.CAMPOS_BASE,
        "modelo_usuario": generation.modelo_usuario_ativo(),
    }


def _minuta_arquivo_atual() -> Path:
    ultima = generation.ultima_minuta()
    if not ultima or not ultima.get("output_path"):
        raise HTTPException(status_code=404, detail="Nenhuma minuta gerada ainda.")
    return Path(ultima["output_path"])


@app.get("/api/minuta/pdf")
def minuta_pdf() -> FileResponse:
    """PDF a partir do DOCX de saída (renderizado pelo LibreOffice)."""
    docx = _minuta_arquivo_atual()
    if not docx.is_file():
        raise HTTPException(status_code=404, detail="Minuta não encontrada.")
    pdf = docx_para_pdf(docx)
    if pdf is None or not pdf.is_file():
        raise HTTPException(status_code=404, detail="Falha ao gerar o PDF da minuta.")
    return FileResponse(str(pdf), media_type="application/pdf", filename=f"{pdf.stem}.pdf")


@app.get("/api/minuta/docx")
def minuta_docx() -> FileResponse:
    """Baixa o próprio arquivo DOCX de saída."""
    docx = _minuta_arquivo_atual()
    if not docx.is_file():
        raise HTTPException(status_code=404, detail="Minuta não encontrada.")
    return FileResponse(
        str(docx),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=docx.name,
    )


@app.post("/api/chat")
def chat(payload: ChatMessage) -> dict:
    """Executa a mensagem no agente e devolve resposta + minuta renderizada."""
    if not payload.message or not payload.message.strip():
        return JSONResponse(
            status_code=400,
            content={"detail": "Mensagem vazia.", "reply": "Envie uma mensagem."},
        )

    if len(payload.message) > MAX_MESSAGE_CHARS:
        return JSONResponse(
            status_code=400,
            content={
                "detail": (
                    f"Mensagem muito longa (máximo de {MAX_MESSAGE_CHARS} "
                    "caracteres)."
                ),
                "reply": "A mensagem é muito longa. Tente resumir o pedido.",
            },
        )

    reply = agent.executar(payload.message)
    return _resposta_chat(reply)


@app.post("/api/conversa/limpar")
def limpar_conversa() -> dict:
    """Reseta o histórico da conversa e o estado de geração."""
    agent.limpar_conversa()
    return {"detail": "Conversa limpa."}


@app.post("/api/upload")
async def upload(arquivo: UploadFile) -> dict:
    """Salva um arquivo do navegador na pasta de importacoes do usuario."""
    nome = Path(arquivo.filename or "arquivo").name
    extensao = Path(nome).suffix.lower()

    if extensao not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Formato '{extensao}' nao suportado. Aceitos: "
            f"{', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    IMPORTACOES_DIR.mkdir(parents=True, exist_ok=True)
    destino = IMPORTACOES_DIR / nome

    with destino.open("wb") as saida:
        while chunk := await arquivo.read(1024 * 256):
            saida.write(chunk)

    return {"filename": nome, "detail": f"Arquivo '{nome}' recebido com sucesso."}


@app.post("/api/modelo")
async def enviar_modelo(arquivo: UploadFile) -> dict:
    """Define um DOCX enviado pelo usuário como modelo de formatação ativo."""
    nome = Path(arquivo.filename or "arquivo").name
    extensao = Path(nome).suffix.lower()

    if extensao != ".docx":
        raise HTTPException(
            status_code=400,
            detail="Somente arquivos .docx podem ser usados como modelo de "
            "formatação (pdf, txt e md não preservam o layout).",
        )

    IMPORTACOES_DIR.mkdir(parents=True, exist_ok=True)
    destino = IMPORTACOES_DIR / nome

    with destino.open("wb") as saida:
        while chunk := await arquivo.read(1024 * 256):
            saida.write(chunk)

    try:
        resumo = generation.set_modelo_usuario(nome, IMPORTACOES_DIR)
    except generation.UserFileError as erro:
        destino.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(erro)) from erro

    return {**resumo, "detail": f"'{nome}' definido como modelo de formatação."}