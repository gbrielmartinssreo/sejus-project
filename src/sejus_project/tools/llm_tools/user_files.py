"""
Tool que lê um arquivo enviado pelo usuário (pasta `importacoes_usuario/`)
e devolve o conteúdo extraído para o agente.

O agente é quem decide, depois de ver o conteúdo, se vai chamar a tool
`consultar_atos_sejus` para comparar com os atos normativos indexados e
apontar o que está faltando na minuta. Este módulo não faz comparação
nem chama nenhum LLM -- só lê e extrai texto.

Formatos suportados: .txt, .md, .pdf, .docx
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]

# Pasta onde os arquivos enviados pelo usuário ficam. Caminho ABSOLUTO a partir
# da raiz do projeto — o mesmo usado pelo endpoint /api/upload — para upload e
# análise sempre enxergarem a mesma pasta, independente do diretório onde o
# servidor for iniciado.
IMPORTACOES_DIR = PROJECT_ROOT / "importacoes_usuario"

# Tamanho da janela de texto devolvida ao agente em CADA leitura. O limite
# antigo (20k) cortava documentos de 10+ páginas na metade. Agora o padrão
# cobre a maioria dos atos por inteiro e, quando o arquivo for maior, a leitura
# é PAGINADA: a tool devolve `offset`/`next_offset`/`has_more` para o agente
# continuar de onde parou (sem perder o restante do documento).
MAX_CHARS = int(os.getenv("USER_FILES_MAX_CHARS", "60000"))

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}

# Upload mais recente registrado na sessão de conversa. O /api/upload grava
# aqui DEPOIS de salvar o arquivo completo, para a tool de análise saber qual
# arquivo o usuário acabou de enviar sem depender do nome aparecer no texto da
# mensagem. O registro é limpo junto com o estado da conversa
# (document_generation.limpar_estado) e é INVALIDADO automaticamente se o
# arquivo sair da pasta.
_UPLOAD_SESSAO: str | None = None


class UserFileError(Exception):
    pass


def registrar_upload(nome: str) -> None:
    """Registra o arquivo recém-enviado como upload da sessão."""
    global _UPLOAD_SESSAO
    _UPLOAD_SESSAO = Path(nome or "").name or None


def upload_sessao() -> str | None:
    """Arquivo registrado como upload da sessão, se ainda existir na pasta."""
    if not _UPLOAD_SESSAO:
        return None
    caminho = IMPORTACOES_DIR / _UPLOAD_SESSAO
    if caminho.is_file() and caminho.suffix.lower() in SUPPORTED_EXTENSIONS:
        return _UPLOAD_SESSAO
    return None


def limpar_upload_sessao() -> None:
    """Descarta o upload registrado (novo ciclo de conversa)."""
    global _UPLOAD_SESSAO
    _UPLOAD_SESSAO = None


def _list_available_files() -> list[str]:
    if not IMPORTACOES_DIR.exists():
        return []
    return sorted(
        f.name for f in IMPORTACOES_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def _ultimo_arquivo_importado() -> str | None:
    """Upload registrado na sessão; senão o arquivo mais recente (por mtime).

    O registro da sessão tem prioridade para que um upload posterior de outro
    fluxo (ex.: modelo de formatação via /api/modelo) não "sequestre" o
    arquivo que o usuário acabou de enviar para análise."""
    registrado = upload_sessao()
    if registrado:
        return registrado
    if not IMPORTACOES_DIR.exists():
        return None
    arquivos = [
        f
        for f in IMPORTACOES_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    if not arquivos:
        return None
    return max(arquivos, key=lambda f: f.stat().st_mtime).name


def _resolve_file(filename: str) -> Path:
    """Resolve o nome do arquivo dentro de importacoes_usuario/, protegendo
    contra path traversal (ex: '../../etc/passwd')."""
    candidate = (IMPORTACOES_DIR / Path(filename).name).resolve()
    base = IMPORTACOES_DIR.resolve()

    if base not in candidate.parents and candidate != base:
        raise UserFileError(f"Caminho inválido: {filename}")

    if not candidate.exists():
        wanted = Path(filename).name.casefold()
        for real_file in IMPORTACOES_DIR.iterdir():
            if (
                real_file.is_file()
                and real_file.name.casefold() == wanted
                and real_file.suffix.lower() in SUPPORTED_EXTENSIONS
            ):
                return real_file.resolve()

        available = _list_available_files()
        hint = f" Arquivos disponíveis: {', '.join(available)}" if available else " Nenhum arquivo encontrado na pasta."
        raise UserFileError(f"Arquivo '{filename}' não encontrado em {IMPORTACOES_DIR}/.{hint}")

    return candidate


def _extract_txt_or_md(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise UserFileError(
            "Leitura de PDF requer a biblioteca 'pypdf'. Instale com: pip install pypdf"
        ) from e

    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages)


def _extract_docx(path: Path) -> str:
    try:
        import docx  # python-docx
    except ImportError as e:
        raise UserFileError(
            "Leitura de DOCX requer a biblioteca 'python-docx'. Instale com: pip install python-docx"
        ) from e

    document = docx.Document(str(path))
    paragraphs = [p.text for p in document.paragraphs]

    # inclui texto de tabelas também, já que minutas costumam ter tabelas
    for table in document.tables:
        for row in table.rows:
            paragraphs.append(" | ".join(cell.text for cell in row.cells))

    return "\n".join(paragraphs)


_EXTRACTORS = {
    ".txt": _extract_txt_or_md,
    ".md": _extract_txt_or_md,
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
}


def extract_file_text(path: Path) -> str:
    """Extrai o texto de um arquivo pelo formato, sem resolver a pasta.

    Diferente de ``read_user_file``, trabalha com um caminho absoluto já
    validado e não trunca o conteúdo (usado por exemplo para carregar um
    DOCX do usuário como modelo de formatação)."""
    extension = path.suffix.lower()
    extractor = _EXTRACTORS.get(extension)
    if extractor is None:
        raise UserFileError(
            f"Formato '{extension}' não suportado. Formatos aceitos: "
            f"{', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    return extractor(path)


def read_user_file(filename: str, offset: int = 0) -> dict:
    """Lê e extrai o conteúdo de um arquivo em importacoes_usuario/.

    Devolve um dict com metadados + texto extraído. Para arquivos maiores que
    ``MAX_CHARS`` a leitura é paginada: ``offset`` seleciona a janela e o
    resultado traz ``next_offset``/``has_more`` para o agente continuar lendo o
    restante do documento (sem truncar conteúdo)."""
    path = _resolve_file(filename)
    extension = path.suffix.lower()

    text = extract_file_text(path)
    total = len(text)

    offset = max(0, int(offset or 0))
    # Offset além do fim: devolve a última janela em vez de vazio.
    if total and offset >= total:
        offset = max(0, total - MAX_CHARS)

    window = text[offset : offset + MAX_CHARS]
    next_offset = offset + len(window)
    has_more = next_offset < total

    return {
        "filename": path.name,
        "extension": extension,
        "n_chars": len(window),
        "n_chars_total": total,
        "offset": offset,
        "next_offset": next_offset if has_more else None,
        "has_more": has_more,
        "truncated": has_more,
        "text": window,
    }


# ---------------------------------------------------------------------------
# Tool para function calling (formato OpenAI, igual às outras tools do projeto)
# ---------------------------------------------------------------------------

definition = {
    "type": "function",
    "function": {
        "name": "analisar_arquivo_usuario",
        "description": (
            "Lê um arquivo enviado pelo usuário (armazenado na pasta "
            "importacoes_usuario/) e devolve o conteúdo extraído em texto. "
            "Use esta ferramenta quando o usuário pedir para avaliar, revisar "
            "ou verificar uma minuta/documento que ele enviou. O upload mais "
            "recente fica registrado na sessão: mesmo que 'filename' esteja "
            "errado ou incompleto, a ferramenta lê o arquivo enviado e avisa "
            "no campo 'aviso' qual nome real foi usado (confie no 'filename' "
            "devolvido — não peça o nome ao usuário de novo). A leitura é "
            "paginada: se o resultado trouxer 'has_more': true, chame a "
            "ferramenta de novo passando 'offset' = 'next_offset' até ler o "
            "documento inteiro antes de concluir a análise. Depois de ler "
            "o conteúdo, se for necessário comparar com as normas da SEJUS "
            "(ex: verificar o que está faltando), chame também a ferramenta "
            "consultar_atos_sejus."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": (
                        "Nome do arquivo dentro da pasta importacoes_usuario/ "
                        "(ex: 'minuta_contrato.pdf'). OPCIONAL: se o usuario "
                        "acabou de importar/enviar e nao informou o nome, "
                        "NAO preencha — a tool usa o upload registrado na "
                        "sessao. Se o nome informado nao existir, a tool "
                        "tambem cai para o upload da sessao e sinaliza no "
                        "campo 'aviso'."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "description": (
                        "OPCIONAL. Posição (em caracteres) de onde iniciar a "
                        "leitura. Preencha com o 'next_offset' devolvido na "
                        "chamada anterior quando 'has_more' for true, para "
                        "continuar lendo um documento grande."
                    ),
                },
            },
        },
    },
}


def analisar_arquivo_usuario(filename: str | None = None, offset: int = 0) -> str:
    """Função exposta ao agente. Sempre devolve uma string (JSON) --
    nunca lança exceção para o chamador, para o agente conseguir reagir
    ao erro (ex: pedir o nome certo do arquivo) em vez de quebrar.

    Sem ``filename``, usa o upload registrado na sessão (ou, na falta dele, a
    importação mais recente da pasta). Com ``filename`` inexistente (nome
    inventado/partial pelo LLM), NÃO falha: cai para o mesmo upload da sessão
    e devolve o campo ``aviso`` com o nome real lido — assim a análise
    reconhece o arquivo já no primeiro turno, sem exigir que o usuário repita
    o nome. A leitura é paginada por ``offset`` para documentos maiores que a
    janela: o agente continua a partir de ``next_offset`` enquanto
    ``has_more`` for true."""
    solicitado = str(filename).strip() if filename else None
    alvo = solicitado or _ultimo_arquivo_importado()

    if not alvo:
        return json.dumps({
            "error": "Nenhum arquivo importado ainda.",
            "available_files": [],
        }, ensure_ascii=False)

    try:
        result = read_user_file(alvo, offset=offset)
    except UserFileError as erro:
        # Nome citado não existe: em vez de devolver "arquivo não encontrado"
        # (loop em que o usuário precisa repetir o nome), usa o upload da
        # sessão/documento e avisa qual arquivo foi lido de fato.
        fallback = _ultimo_arquivo_importado()
        if not solicitado or not fallback or fallback == Path(alvo).name:
            return json.dumps({"error": str(erro)}, ensure_ascii=False)
        try:
            result = read_user_file(fallback, offset=offset)
        except UserFileError as erro_fallback:
            return json.dumps(
                {"error": str(erro_fallback)},
                ensure_ascii=False,
            )
        result["filename_solicitado"] = solicitado
        result["aviso"] = (
            f"Arquivo '{solicitado}' não encontrado; usando o upload mais "
            f"recente da sessão: '{fallback}'."
        )

    return json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Uso: python user_files.py <nome_do_arquivo>")
        print(f"Arquivos disponíveis em {IMPORTACOES_DIR}/: {_list_available_files()}")
        sys.exit(1)

    output = analisar_arquivo_usuario(sys.argv[1])
    parsed = json.loads(output)
    if "error" in parsed:
        print(f"ERRO: {parsed['error']}")
    else:
        print(f"Arquivo: {parsed['filename']} ({parsed['n_chars']} caracteres, truncado={parsed['truncated']})")
        print("---")
        print(parsed["text"][:1000])