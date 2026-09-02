"""
chunking.py
-----------
Responsabilidade: DIVIDIR documentos (já preparados pelo ingestion.py) em
chunks prontos para embedding.

Recebe `Document` (de ingestion.py) com texto já limpo, e faz:
  1. Separação em "atos administrativos" (Portaria, Instrução Normativa,
     Decreto, Extrato, etc.) -- um único arquivo pode conter vários atos e
     órgãos diferentes misturados
  2. Dentro de cada ato: tabelas viram um chunk único bruto; o texto
     restante é dividido por "Art. Xº"; blocos ainda grandes demais caem
     em um fallback por tamanho de tokens (aproximado) com overlap

Uso:
    from ingestion import load_documents
    from chunking import chunk_documents

    docs = load_documents("../../docs/fontes-rag/markdown/")
    chunks = chunk_documents(docs)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict

from sejus_project.rag.ingestion import Document, load_documents

# ---------------------------------------------------------------------------
# 1. Separação em atos administrativos
# ---------------------------------------------------------------------------

ACT_TYPES = [
    "INSTRUÇÃO NORMATIVA",
    "PORTARIA CONJUNTA",
    "PORTARIA",
    "DECRETO",
    "RESOLUÇÃO",
    "EXTRATO DO SEGUNDO TERMO ADITIVO",
    "EXTRATO DO TERCEIRO TERMO ADITIVO",
    "EXTRATO DO PRIMEIRO TERMO ADITIVO",
    "EXTRATO DA PORTARIA",
    "EXTRATO DE",
    "EXTRATO DO",
    "EDITAL",
    "AVISO",
    "COMUNICADO",
    "ERRATA",
    "RETIFICAÇÃO",
]
_TYPES_PATTERN = "|".join(re.escape(t) for t in ACT_TYPES)
RE_ACT_HEADER = re.compile(
    rf"^\s*({_TYPES_PATTERN})\s*(N[ºo°.]{{0,3}}\s*[\w./-]+)?.*$", re.MULTILINE
)


@dataclass
class Act:
    header: str
    act_type: str
    act_number: str
    text: str
    order: int = 0


def _guess_type(header_line: str) -> str:
    for t in ACT_TYPES:
        if header_line.upper().startswith(t):
            return t
    return "DESCONHECIDO"


def _guess_number(header_line: str) -> str:
    m = re.search(r"N[ºo°.]{0,3}\s*([\w./-]+)", header_line)
    return m.group(1) if m else ""


def split_into_acts(text: str, filename: str) -> list[Act]:
    matches = list(RE_ACT_HEADER.finditer(text))

    if not matches:
        return [Act(header=filename, act_type="DESCONHECIDO", act_number="", text=text, order=0)]

    acts = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk_text = text[start:end].strip()
        header_line = m.group(0).strip()
        acts.append(Act(
            header=header_line,
            act_type=_guess_type(header_line),
            act_number=_guess_number(header_line),
            text=chunk_text,
            order=i,
        ))

    preamble = text[:matches[0].start()].strip()
    if len(preamble) > 30:
        acts.insert(0, Act(header="PREÂMBULO/METADADOS", act_type="PREAMBULO",
                            act_number="", text=preamble, order=-1))

    return acts


# ---------------------------------------------------------------------------
# 2. Split de conteúdo (tabelas + artigos + fallback por tamanho)
# ---------------------------------------------------------------------------

CHUNK_SIZE_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 80

RE_ARTIGO = re.compile(r"(?=^\s*Art\.?\s*\d+[ºo°.]?\s)", re.MULTILINE)
RE_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")


def count_tokens(text: str) -> int:
    """Aproximação: ~4 caracteres por token. Troque por um tokenizer real
    (ex: tiktoken ou o tokenizer do seu modelo de embedding) se precisar
    de precisão maior."""
    return max(1, len(text) // 4)


@dataclass
class ContentBlock:
    kind: str  # "table" | "text"
    text: str


def _extract_table_blocks(text: str) -> list[ContentBlock]:
    lines = text.split("\n")
    blocks: list[ContentBlock] = []
    buf: list[str] = []
    mode = "text"

    def flush():
        nonlocal buf
        if buf:
            content = "\n".join(buf).strip()
            if content:
                blocks.append(ContentBlock(kind=mode, text=content))
        buf = []

    blank_streak = 0
    for line in lines:
        is_table_line = bool(RE_TABLE_LINE.match(line))
        is_blank = not line.strip()

        if is_table_line:
            if mode == "text":
                flush()
                mode = "table"
            buf.append(line)
            blank_streak = 0
        elif mode == "table":
            if is_blank:
                blank_streak += 1
                buf.append(line)
                if blank_streak >= 2:
                    flush()
                    mode = "text"
                    blank_streak = 0
            else:
                buf.append(line)
                blank_streak = 0
        else:
            buf.append(line)

    flush()
    return blocks


def _split_text_block(text: str) -> list[str]:
    parts = RE_ARTIGO.split(text)
    parts = [p.strip() for p in parts if p.strip()]
    return parts if parts else ([text.strip()] if text.strip() else [])


def _split_by_tokens(text: str) -> list[str]:
    if count_tokens(text) <= CHUNK_SIZE_TOKENS:
        return [text]

    paragraphs = re.split(r"\n\s*\n", text)
    chunks = []
    current = ""
    for p in paragraphs:
        candidate = (current + "\n\n" + p).strip() if current else p
        if count_tokens(candidate) > CHUNK_SIZE_TOKENS and current:
            chunks.append(current.strip())
            overlap_words = current.split()[-CHUNK_OVERLAP_TOKENS:]
            current = " ".join(overlap_words) + "\n\n" + p
        else:
            current = candidate
    if current.strip():
        chunks.append(current.strip())
    return chunks


def split_act_content(act_text: str) -> list[ContentBlock]:
    raw_blocks = _extract_table_blocks(act_text)
    final_blocks: list[ContentBlock] = []

    for block in raw_blocks:
        if block.kind == "table":
            final_blocks.append(block)
            continue
        for article_piece in _split_text_block(block.text):
            for sub_piece in _split_by_tokens(article_piece):
                final_blocks.append(ContentBlock(kind="text", text=sub_piece))

    return final_blocks


# ---------------------------------------------------------------------------
# 3. API pública: Chunk + chunk_document / chunk_documents
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    id: str
    source_file: str
    act_type: str
    act_number: str
    act_header: str
    block_kind: str
    block_index: int
    n_tokens: int
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


def chunk_document(doc: Document) -> list[Chunk]:
    """Gera os chunks de um único documento já preparado (ver ingestion.py)."""
    acts = split_into_acts(doc.text, doc.source_file)

    chunks: list[Chunk] = []
    for act in acts:
        blocks = split_act_content(act.text)
        for i, block in enumerate(blocks):
            chunks.append(Chunk(
                id=f"{doc.source_file}::act{act.order}::{block.kind}{i}",
                source_file=doc.source_file,
                act_type=act.act_type,
                act_number=act.act_number,
                act_header=act.header[:200],
                block_kind=block.kind,
                block_index=i,
                n_tokens=count_tokens(block.text),
                text=block.text,
            ))
    return chunks


def chunk_documents(docs: list[Document]) -> list[Chunk]:
    """Gera os chunks de uma lista de documentos já preparados."""
    all_chunks: list[Chunk] = []
    for doc in docs:
        all_chunks.extend(chunk_document(doc))
    return all_chunks


def save_chunks_jsonl(chunks: list[Chunk], output_path: str) -> None:
    with open(output_path, "w", encoding="utf-8") as out:
        for c in chunks:
            out.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Pasta com os .md")
    parser.add_argument("--output", default="chunks.jsonl")
    args = parser.parse_args()

    docs = load_documents(args.input)
    chunks = chunk_documents(docs)
    save_chunks_jsonl(chunks, args.output)
    print(f"{len(docs)} documentos -> {len(chunks)} chunks salvos em {args.output}")