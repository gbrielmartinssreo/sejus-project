"""
chunking.py
-----------
Responsabilidade: DIVIDIR documentos (já preparados pelo ingestion.py) em
chunks prontos para embedding.

Recebe `Document` (de ingestion.py) com texto já limpo, e faz:
  1. Separação em "atos administrativos" (Portaria, Instrução Normativa,
     Decreto, Extrato, etc.) -- um único arquivo pode conter vários atos e
     órgãos diferentes misturados
  2. Dentro de cada ato:
       - tabelas são preservadas como chunks do tipo "table"
       - texto é dividido por "Art. Xº"
       - blocos grandes são divididos por tamanho de tokens
       - parágrafos individuais grandes também são divididos
       - tabelas grandes também são divididas
  3. O tamanho dos chunks é calculado usando o tokenizer cl100k_base,
     compatível com text-embedding-3-small.

Uso:
    from sejus_project.rag.ingestion import load_documents
    from sejus_project.rag.chunking import chunk_documents

    docs = load_documents("../../docs/fontes-rag/markdown/")
    chunks = chunk_documents(docs)
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

import tiktoken

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
    rf"^\s*({_TYPES_PATTERN})\s*"
    rf"(N[ºo°.]{{0,3}}\s*[\w./-]+)?.*$",
    re.MULTILINE,
)


@dataclass
class Act:
    header: str
    act_type: str
    act_number: str
    text: str
    order: int = 0


def _guess_type(header_line: str) -> str:
    for act_type in ACT_TYPES:
        if header_line.upper().startswith(act_type):
            return act_type

    return "DESCONHECIDO"


def _guess_number(header_line: str) -> str:
    match = re.search(
        r"N[ºo°.]{0,3}\s*([\w./-]+)",
        header_line,
    )

    return match.group(1) if match else ""


def split_into_acts(text: str, filename: str) -> list[Act]:
    matches = list(RE_ACT_HEADER.finditer(text))

    if not matches:
        return [
            Act(
                header=filename,
                act_type="DESCONHECIDO",
                act_number="",
                text=text,
                order=0,
            )
        ]

    acts: list[Act] = []

    for i, match in enumerate(matches):
        start = match.start()

        if i + 1 < len(matches):
            end = matches[i + 1].start()
        else:
            end = len(text)

        chunk_text = text[start:end].strip()
        header_line = match.group(0).strip()

        acts.append(
            Act(
                header=header_line,
                act_type=_guess_type(header_line),
                act_number=_guess_number(header_line),
                text=chunk_text,
                order=i,
            )
        )

    # Texto que aparece antes do primeiro ato.
    preamble = text[: matches[0].start()].strip()

    if len(preamble) > 30:
        acts.insert(
            0,
            Act(
                header="PREÂMBULO/METADADOS",
                act_type="PREAMBULO",
                act_number="",
                text=preamble,
                order=-1,
            ),
        )

    return acts


# ---------------------------------------------------------------------------
# 2. Configuração de chunks
# ---------------------------------------------------------------------------

# Tamanho desejado dos chunks.
CHUNK_SIZE_TOKENS = 500

# Quantidade de tokens reaproveitados entre chunks.
CHUNK_OVERLAP_TOKENS = 80

# Tokenizer utilizado para medir os chunks.
# text-embedding-3-small aceita até 8192 tokens.
TOKENIZER = tiktoken.get_encoding("cl100k_base")


RE_ARTIGO = re.compile(
    r"(?=^\s*Art\.?\s*\d+[ºo°.]?\s)",
    re.MULTILINE,
)

RE_TABLE_LINE = re.compile(
    r"^\s*\|.*\|\s*$"
)


def count_tokens(text: str) -> int:
    """
    Conta tokens usando o tokenizer cl100k_base.

    Esse tokenizer é adequado para estimar o tamanho de entrada
    usado pelo text-embedding-3-small.
    """
    if not text:
        return 0

    return len(TOKENIZER.encode(text))


@dataclass
class ContentBlock:
    kind: str  # "table" | "text"
    text: str


# ---------------------------------------------------------------------------
# 3. Extração de tabelas
# ---------------------------------------------------------------------------

def _extract_table_blocks(text: str) -> list[ContentBlock]:
    """
    Separa o conteúdo em blocos de texto e tabelas.

    Linhas no formato Markdown:
        | coluna | coluna |

    são tratadas como tabelas.
    """

    lines = text.split("\n")

    blocks: list[ContentBlock] = []

    buf: list[str] = []
    mode = "text"

    def flush() -> None:
        nonlocal buf

        if not buf:
            return

        content = "\n".join(buf).strip()

        if content:
            blocks.append(
                ContentBlock(
                    kind=mode,
                    text=content,
                )
            )

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

                # Duas linhas vazias encerram a tabela.
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


# ---------------------------------------------------------------------------
# 4. Separação por artigos
# ---------------------------------------------------------------------------

def _split_text_block(text: str) -> list[str]:
    """
    Divide um bloco de texto a partir dos artigos.

    Exemplo:

        Art. 1º ...
        Art. 2º ...
        Art. 3º ...

    vira:

        [Art. 1º ..., Art. 2º ..., Art. 3º ...]
    """

    parts = RE_ARTIGO.split(text)

    parts = [
        part.strip()
        for part in parts
        if part.strip()
    ]

    if parts:
        return parts

    return [text.strip()] if text.strip() else []


# ---------------------------------------------------------------------------
# 5. Fallback por tamanho de tokens
# ---------------------------------------------------------------------------

def _split_by_tokens(text: str) -> list[str]:
    """
    Divide um texto para que cada chunk fique próximo de
    CHUNK_SIZE_TOKENS.

    Também trata o caso em que um único parágrafo já é maior
    que o limite.
    """

    if not text.strip():
        return []

    if count_tokens(text) <= CHUNK_SIZE_TOKENS:
        return [text.strip()]

    paragraphs = re.split(
        r"\n\s*\n",
        text,
    )

    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        paragraph = paragraph.strip()

        if not paragraph:
            continue

        # ---------------------------------------------------------------
        # Caso 1:
        # O parágrafo sozinho é maior que o limite.
        # ---------------------------------------------------------------

        if count_tokens(paragraph) > CHUNK_SIZE_TOKENS:

            # Primeiro salva o que já estava acumulado.
            if current.strip():
                chunks.append(current.strip())
                current = ""

            words = paragraph.split()

            word_chunk: list[str] = []

            for word in words:
                candidate = " ".join(
                    word_chunk + [word]
                )

                if count_tokens(candidate) > CHUNK_SIZE_TOKENS:

                    if word_chunk:
                        chunks.append(
                            " ".join(word_chunk).strip()
                        )

                    # Overlap por palavras.
                    overlap = word_chunk[
                        -CHUNK_OVERLAP_TOKENS:
                    ]

                    word_chunk = overlap + [word]

                else:
                    word_chunk.append(word)

            if word_chunk:
                current = " ".join(word_chunk)

            continue

        # ---------------------------------------------------------------
        # Caso 2:
        # Parágrafo cabe sozinho, então tentamos juntar ao atual.
        # ---------------------------------------------------------------

        if current:
            candidate = (
                f"{current}\n\n{paragraph}"
            )
        else:
            candidate = paragraph

        if count_tokens(candidate) > CHUNK_SIZE_TOKENS:

            # Salva o chunk atual.
            if current.strip():
                chunks.append(current.strip())

            # Cria overlap usando palavras do chunk anterior.
            overlap_words = current.split()[
                -CHUNK_OVERLAP_TOKENS:
            ]

            if overlap_words:
                current = (
                    " ".join(overlap_words)
                    + "\n\n"
                    + paragraph
                )
            else:
                current = paragraph

        else:
            current = candidate

    if current.strip():
        chunks.append(current.strip())

    return chunks


# ---------------------------------------------------------------------------
# 6. Split de conteúdo de um ato
# ---------------------------------------------------------------------------

def split_act_content(act_text: str) -> list[ContentBlock]:
    """
    Divide o conteúdo de um ato em blocos de texto e tabelas.

    Nenhum bloco deve ultrapassar CHUNK_SIZE_TOKENS.
    """

    raw_blocks = _extract_table_blocks(act_text)

    final_blocks: list[ContentBlock] = []

    for block in raw_blocks:

        # ---------------------------------------------------------------
        # TABELAS
        # ---------------------------------------------------------------

        if block.kind == "table":

            # Antes as tabelas eram adicionadas diretamente:
            #
            #     final_blocks.append(block)
            #
            # Isso permitia que uma tabela tivesse 10k, 20k ou mais
            # tokens e causasse erro na API da OpenAI.
            #
            # Agora tabelas grandes também passam pelo splitter.

            pieces = _split_by_tokens(block.text)

            for piece in pieces:
                final_blocks.append(
                    ContentBlock(
                        kind="table",
                        text=piece,
                    )
                )

            continue

        # ---------------------------------------------------------------
        # TEXTO
        # ---------------------------------------------------------------

        article_pieces = _split_text_block(block.text)

        for article_piece in article_pieces:

            sub_pieces = _split_by_tokens(
                article_piece
            )

            for sub_piece in sub_pieces:
                final_blocks.append(
                    ContentBlock(
                        kind="text",
                        text=sub_piece,
                    )
                )

    return final_blocks


# ---------------------------------------------------------------------------
# 7. Chunk final
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


# ---------------------------------------------------------------------------
# 8. Chunk de um documento
# ---------------------------------------------------------------------------

def chunk_document(doc: Document) -> list[Chunk]:
    """
    Gera os chunks de um único documento já preparado.
    """

    acts = split_into_acts(
        doc.text,
        doc.source_file,
    )

    chunks: list[Chunk] = []

    for act in acts:

        blocks = split_act_content(
            act.text
        )

        for i, block in enumerate(blocks):

            n_tokens = count_tokens(
                block.text
            )

            # Segurança adicional.
            #
            # Se por algum motivo o splitter deixar passar
            # um bloco grande, não permitimos criar um Chunk
            # acima do limite configurado.

            if n_tokens > CHUNK_SIZE_TOKENS:

                emergency_pieces = _split_by_tokens(
                    block.text
                )

                for j, piece in enumerate(
                    emergency_pieces
                ):
                    chunks.append(
                        Chunk(
                            id=(
                                f"{doc.source_file}"
                                f"::act{act.order}"
                                f"::{block.kind}{i}_{j}"
                            ),
                            source_file=doc.source_file,
                            act_type=act.act_type,
                            act_number=act.act_number,
                            act_header=act.header[:200],
                            block_kind=block.kind,
                            block_index=i,
                            n_tokens=count_tokens(
                                piece
                            ),
                            text=piece,
                        )
                    )

                continue

            chunks.append(
                Chunk(
                    id=(
                        f"{doc.source_file}"
                        f"::act{act.order}"
                        f"::{block.kind}{i}"
                    ),
                    source_file=doc.source_file,
                    act_type=act.act_type,
                    act_number=act.act_number,
                    act_header=act.header[:200],
                    block_kind=block.kind,
                    block_index=i,
                    n_tokens=n_tokens,
                    text=block.text,
                )
            )

    return chunks


# ---------------------------------------------------------------------------
# 9. Chunk de vários documentos
# ---------------------------------------------------------------------------

def chunk_documents(
    docs: list[Document],
) -> list[Chunk]:
    """
    Gera chunks de uma lista de documentos.
    """

    all_chunks: list[Chunk] = []

    for doc in docs:
        all_chunks.extend(
            chunk_document(doc)
        )

    return all_chunks


# ---------------------------------------------------------------------------
# 10. Salvar JSONL
# ---------------------------------------------------------------------------

def save_chunks_jsonl(
    chunks: list[Chunk],
    output_path: str,
) -> None:
    """
    Salva os chunks em formato JSONL.
    """

    with open(
        output_path,
        "w",
        encoding="utf-8",
    ) as out:

        out.writelines(json.dumps(
                    chunk.to_dict(),
                    ensure_ascii=False,
                )
                + "\n" for chunk in chunks)


# ---------------------------------------------------------------------------
# 11. Execução direta
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--input",
        required=True,
        help="Pasta com os .md",
    )

    parser.add_argument(
        "--output",
        default="chunks.jsonl",
    )

    args = parser.parse_args()

    docs = load_documents(
        args.input
    )

    chunks = chunk_documents(
        docs
    )

    save_chunks_jsonl(
        chunks,
        args.output,
    )

    print(
        f"{len(docs)} documentos -> "
        f"{len(chunks)} chunks salvos em "
        f"{args.output}"
    )

    if chunks:
        token_counts = [
            chunk.n_tokens
            for chunk in chunks
        ]

        print(
            f"Menor chunk: "
            f"{min(token_counts)} tokens"
        )

        print(
            f"Maior chunk: "
            f"{max(token_counts)} tokens"
        )

        print(
            f"Média: "
            f"{sum(token_counts) / len(token_counts):.1f} tokens"
        )

        oversized = [
            chunk
            for chunk in chunks
            if chunk.n_tokens > 8192
        ]

        print(
            f"Chunks acima de 8192 tokens: "
            f"{len(oversized)}"
        )
