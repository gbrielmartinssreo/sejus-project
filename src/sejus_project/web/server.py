"""Servidor web local do chat SEJUS (interface em vez da CLI).

Endpoint principal: ``POST /api/chat`` chama ``agent.executar`` e devolve a
resposta em markdown junto com a ultima minuta gerada (renderizada em HTML),
quando houver. O upload de arquivos alimenta a pasta ``importacoes_usuario/``
usada pela tool ``analisar_arquivo_usuario``.

Rodar local:
    uv run uvicorn sejus_project.web.server:app --reload
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sejus_project.agent import agent
from sejus_project.tools import document_generation as generation
from sejus_project.tools.user_files import SUPPORTED_EXTENSIONS
from sejus_project.web.render_html import minuta_para_html, minuta_para_texto

STATIC_DIR = Path(__file__).parent / "static"
IMPORTACOES_DIR = Path(__file__).resolve().parents[3] / "importacoes_usuario"

app = FastAPI(title="SEJUS Chat", docs_url="/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ChatMessage(BaseModel):
    message: str


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.post("/api/chat")
def chat(payload: ChatMessage) -> dict:
    """Executa a mensagem no agente e devolve resposta + minuta renderizada."""
    reply = agent.executar(payload.message)

    minuta_html = None
    minuta_texto = None
    ultima = generation.ultima_minuta()
    if ultima:
        minuta_html = minuta_para_html(ultima["estructura"])
        minuta_texto = minuta_para_texto(ultima["estructura"])

    return {
        "reply": reply,
        "minuta_html": minuta_html,
        "minuta_texto": minuta_texto,
        "pendente": generation.has_pending_document(),
        "campos": generation.CAMPOS_BASE,
    }


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