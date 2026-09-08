from pathlib import Path

from sejus_project.rag.ingestion import load_documents
from sejus_project.rag.chunking import chunk_documents
from sejus_project.rag.embedding import Embedder
from sejus_project.rag.indexing import (
    QdrantIndexer,
    QdrantIndexerConfig,
)


# Definir o diretório raiz do projeto
script_dir = Path(__file__).parent
projeto_root = script_dir.parent


# ---------------------------------------------------------------------------
# 1. Ler e limpar os .md
# ---------------------------------------------------------------------------

docs = load_documents(
    projeto_root
    / "docs"
    / "fontes-rag"
    / "markdown"
)

print(
    f"{len(docs)} documentos carregados"
)


# ---------------------------------------------------------------------------
# 2. Dividir em chunks
# ---------------------------------------------------------------------------

chunks = chunk_documents(docs)

print(
    f"{len(chunks)} chunks gerados"
)

if chunks:
    token_counts = [
        chunk.n_tokens
        for chunk in chunks
    ]

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

    if oversized:
        raise RuntimeError(
            f"Foram encontrados "
            f"{len(oversized)} chunks acima de "
            f"8192 tokens. "
            f"Corrija o chunking antes de enviar "
            f"para a OpenAI."
        )

    print(
        "Nenhum chunk ultrapassa "
        "o limite de 8192 tokens."
    )


# ---------------------------------------------------------------------------
# 3. Gerar embeddings + indexar no Qdrant
# ---------------------------------------------------------------------------

# Embeddings são gerados pela OpenAI API.
embedder = Embedder()


indexer = QdrantIndexer(
    QdrantIndexerConfig(
        collection_name="sejus_atos",
        vector_size=embedder.dimension,
    )
)


# True = apaga e recria a coleção do zero.
#
# Isso é necessário ao trocar o modelo de embedding,
# pois os vetores antigos não são compatíveis com os novos.
indexer.create_collection(
    recreate=True
)


# Gera os embeddings e envia os chunks para o Qdrant.
indexer.index_chunks(
    chunks,
    embedder,
)
