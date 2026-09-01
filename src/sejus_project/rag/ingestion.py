"""
ingestion.py
------------
Responsabilidade: ENTRADA e PREPARAÇÃO dos documentos.

- Lê os arquivos .md de uma pasta (ex: fontes-rag/)
- Limpa o ruído específico da extração PDF -> MD do Diário Oficial de MT
  (tags de sistema, cabeçalho/rodapé, protocolo, espaçamento irregular)
- Devolve uma lista de `Document`, prontos para o chunking.py consumir

Não faz chunking, não faz embedding, não indexa nada -- só prepara o
texto bruto.

Uso:
    from ingestion import load_documents

    docs = load_documents("fontes-rag/")
    for d in docs:
        print(d.source_file, len(d.text))
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Limpeza de ruído específico do Diário Oficial de MT
# ---------------------------------------------------------------------------

RE_SYSTEM_TAG = re.compile(r"<#[A-Z.]+#\d+#\d+#\d+/?>")
RE_PROTOCOLO = re.compile(r"^\s*Protocolo\s+\d+\s*$", re.MULTILINE)
RE_IOMAT_FOOTER = re.compile(
    r"GOVERNO DO ESTADO DE MATO GROSSO.*?Código de Autenticidade:\s*\w+",
    re.DOTALL,
)
RE_DATE_LINE = re.compile(
    r"^\s*\d{1,2}\s+de\s+[a-zç]+\s+de\s+\d{4}\s*$", re.MULTILINE | re.IGNORECASE
)
RE_DIARIO_OFICIAL = re.compile(r"^\s*Di[aá]rio\s+Oficial\s*$", re.MULTILINE | re.IGNORECASE)
RE_PAGINA = re.compile(r"^\s*P[aá]gina\s+\d+\s*$", re.MULTILINE | re.IGNORECASE)
RE_EDICAO = re.compile(r"^\s*N[ºo°]\s*[\d.]+\s*$", re.MULTILINE)
RE_ORGAO_ISOLADO = re.compile(
    r"^\s*(SEJUS|PMMT|POLITEC|CBM|SEPLAG|FUNAC|SESP|SEDUC|SEFAZ|SES|INDEA|SEAF)\s*$",
    re.MULTILINE,
)
RE_MULTI_SPACE = re.compile(r"[ \t]{2,}")
RE_MULTI_NEWLINE = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """Remove ruído típico da extração PDF->MD do Diário Oficial de MT."""
    text = RE_SYSTEM_TAG.sub("", text)
    text = RE_IOMAT_FOOTER.sub("", text)
    text = RE_PROTOCOLO.sub("", text)
    text = RE_DATE_LINE.sub("", text)
    text = RE_DIARIO_OFICIAL.sub("", text)
    text = RE_PAGINA.sub("", text)
    text = RE_EDICAO.sub("", text)
    text = RE_ORGAO_ISOLADO.sub("", text)
    text = RE_MULTI_SPACE.sub(" ", text)
    text = RE_MULTI_NEWLINE.sub("\n\n", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = RE_MULTI_NEWLINE.sub("\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Documento preparado
# ---------------------------------------------------------------------------

@dataclass
class Document:
    source_file: str   # caminho relativo do arquivo original
    raw_text: str        # conteúdo bruto, como veio do .md
    text: str             # conteúdo já limpo, pronto para o chunking


def load_document(filepath: Path, root: Path | None = None) -> Document:
    """Lê e prepara um único arquivo .md."""
    root = root or filepath.parent
    raw_text = filepath.read_text(encoding="utf-8", errors="ignore")
    try:
        relative_path = str(filepath.relative_to(root))
    except ValueError:
        relative_path = filepath.name

    return Document(
        source_file=relative_path,
        raw_text=raw_text,
        text=clean_text(raw_text),
    )


def load_documents(input_dir: str | Path, pattern: str = "*.md") -> list[Document]:
    """Lê e prepara todos os .md de uma pasta (recursivo)."""
    root = Path(input_dir)
    files = sorted(root.rglob(pattern))
    return [load_document(f, root) for f in files]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Pasta com os .md")
    args = parser.parse_args()

    docs = load_documents(args.input)
    print(f"{len(docs)} documentos carregados e limpos")
    for d in docs[:3]:
        print(f"  {d.source_file}: {len(d.text)} caracteres (era {len(d.raw_text)})")