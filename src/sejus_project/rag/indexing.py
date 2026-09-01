"""
indexing.py
-----------
Cria/alimenta uma coleção no Qdrant com os chunks + embeddings.

Não requer Docker nem servidor rodando: por padrão usa o modo EMBARCADO
do qdrant-client, que persiste os dados direto em disco (pasta local
"./qdrant_data"). É o mesmo Qdrant, só que embutido no processo Python.

Se no futuro você tiver um servidor Qdrant (Docker ou Qdrant Cloud), basta
preencher "url" (e "api_key", se for Cloud) no QdrantIndexerConfig.

Uso:
    from ingestion import load_documents
    from chunking import chunk_documents
    from embedding import Embedder
    from indexing import QdrantIndexer, QdrantIndexerConfig

    chunks = chunk_documents(load_documents("fontes-rag/"))
    embedder = Embedder()

    indexer = QdrantIndexer(QdrantIndexerConfig(
        collection_name="sejus_atos",
        vector_size=embedder.dimension,
    ))
    indexer.create_collection(recreate=True)  # cuidado: apaga se já existir
    indexer.index_chunks(chunks, embedder)

    # Depois, para buscar:
    results = indexer.search(embedder.embed_query("prazo do grupo de trabalho"), limit=5)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

# Import só para type hints, evita import circular em runtime se não precisar
try:
    from chunking import Chunk
except ImportError:
    Chunk = None  # type: ignore


@dataclass
class QdrantIndexerConfig:
    collection_name: str = "sejus_atos"
    vector_size: int = 1024  # deve bater com embedder.dimension
    distance: qmodels.Distance = qmodels.Distance.COSINE
    batch_size: int = 64

    # --- Modo de conexão (escolha UM dos três) ---
    # 1) Embutido, salvando em disco (RECOMENDADO sem Docker):
    path: str | None = "./qdrant_data"
    # 2) Embutido, só em memória (dados somem ao encerrar o processo):
    #    deixe path=None e in_memory=True
    in_memory: bool = False
    # 3) Servidor remoto/Docker/Qdrant Cloud (deixe path=None, in_memory=False):
    url: str | None = None
    api_key: str | None = None


class QdrantIndexer:
    def __init__(self, config: QdrantIndexerConfig | None = None):
        self.config = config or QdrantIndexerConfig()

        if self.config.url:
            # Servidor remoto (Docker local ou Qdrant Cloud)
            self.client = QdrantClient(url=self.config.url, api_key=self.config.api_key)
        elif self.config.in_memory:
            # Embutido, só em memória -- bom para testes rápidos
            self.client = QdrantClient(":memory:")
        else:
            # Embutido, persistido em disco -- não precisa de Docker
            self.client = QdrantClient(path=self.config.path)

    def create_collection(self, recreate: bool = False) -> None:
        exists = self.client.collection_exists(self.config.collection_name)
        if exists and not recreate:
            return
        if exists and recreate:
            self.client.delete_collection(self.config.collection_name)

        self.client.create_collection(
            collection_name=self.config.collection_name,
            vectors_config=qmodels.VectorParams(
                size=self.config.vector_size,
                distance=self.config.distance,
            ),
        )
        # Índices de payload para permitir filtrar por metadado nas buscas
        # (ex: "só Instrução Normativa", "só um arquivo específico")
        for field_name in ("act_type", "source_file", "act_number", "block_kind"):
            self.client.create_payload_index(
                collection_name=self.config.collection_name,
                field_name=field_name,
                field_schema=qmodels.PayloadSchemaType.KEYWORD,
            )

    def index_chunks(self, chunks: list["Chunk"], embedder) -> None:
        """Gera embeddings em lote e envia para o Qdrant em batches."""
        texts = [c.text for c in chunks]
        vectors = embedder.embed_passages(texts)

        batch_size = self.config.batch_size
        for start in range(0, len(chunks), batch_size):
            batch_chunks = chunks[start:start + batch_size]
            batch_vectors = vectors[start:start + batch_size]

            points = [
                qmodels.PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, c.id)),  # id determinístico
                    vector=vec,
                    payload={
                        "chunk_id": c.id,
                        "source_file": c.source_file,
                        "act_type": c.act_type,
                        "act_number": c.act_number,
                        "act_header": c.act_header,
                        "block_kind": c.block_kind,
                        "block_index": c.block_index,
                        "n_tokens": c.n_tokens,
                        "text": c.text,
                    },
                )
                for c, vec in zip(batch_chunks, batch_vectors)
            ]

            self.client.upsert(collection_name=self.config.collection_name, points=points)
            print(f"  indexados {start + len(points)}/{len(chunks)} chunks")

    def search(
        self,
        query_vector: list[float],
        limit: int = 5,
        act_type: str | None = None,
        source_file: str | None = None,
    ) -> list[dict]:
        """Busca por similaridade, com filtros opcionais de metadado."""
        must = []
        if act_type:
            must.append(qmodels.FieldCondition(
                key="act_type", match=qmodels.MatchValue(value=act_type)
            ))
        if source_file:
            must.append(qmodels.FieldCondition(
                key="source_file", match=qmodels.MatchValue(value=source_file)
            ))
        query_filter = qmodels.Filter(must=must) if must else None

        results = self.client.query_points(
            collection_name=self.config.collection_name,
            query=query_vector,
            limit=limit,
            query_filter=query_filter,
        )
        return [
            {"score": p.score, **p.payload}
            for p in results.points
        ]


if __name__ == "__main__":
    # Uso isolado para testar/depurar só a indexação a partir de um
    # chunks.jsonl já existente (gerado pelo chunking.py). A orquestração
    # completa (ingestion -> chunking -> embedding -> indexing) fica fora
    # deste arquivo, já que não é responsabilidade do indexing.py.
    import argparse
    import json
    from chunking import Chunk
    from embedding import Embedder

    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks", required=True, help="Arquivo chunks.jsonl gerado pelo chunking.py")
    parser.add_argument("--collection", default="sejus_atos")
    parser.add_argument("--recreate", action="store_true")
    args = parser.parse_args()

    with open(args.chunks, encoding="utf-8") as f:
        chunks = [Chunk(**json.loads(line)) for line in f]
    print(f"{len(chunks)} chunks carregados de {args.chunks}")

    embedder = Embedder()
    indexer = QdrantIndexer(QdrantIndexerConfig(
        collection_name=args.collection,
        vector_size=embedder.dimension,
    ))
    indexer.create_collection(recreate=args.recreate)
    indexer.index_chunks(chunks, embedder)
    print("Indexação concluída.")