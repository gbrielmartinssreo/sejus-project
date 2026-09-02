"""
retrieval.py
------------
Responsabilidade: RECUPERAR os chunks relevantes para o agente.

O agente usa o Groq para decidir quando chamar a tool e para sintetizar a
resposta. Este módulo não chama nenhum LLM.

Uso standalone:
    python retrieval.py "qual o prazo do grupo de trabalho?"
"""
from __future__ import annotations

import json
import atexit
from dataclasses import dataclass

from sejus_project.rag.embedding import Embedder
from sejus_project.rag.indexing import QdrantIndexer, QdrantIndexerConfig

DEFAULT_COLLECTION = "sejus_atos"
DEFAULT_LIMIT = 5

# Instâncias reaproveitadas entre chamadas, para não recarregar o modelo de
# embedding nem reabrir o Qdrant a cada pergunta.
_embedder: Embedder | None = None
_indexer: QdrantIndexer | None = None


def _close_indexer() -> None:
    if _indexer is not None:
        _indexer.client.close()


atexit.register(_close_indexer)


def _get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder


def _get_indexer(collection_name: str = DEFAULT_COLLECTION) -> QdrantIndexer:
    global _indexer
    if _indexer is None or _indexer.config.collection_name != collection_name:
        _indexer = QdrantIndexer(QdrantIndexerConfig(collection_name=collection_name))
    return _indexer


# ---------------------------------------------------------------------------
# 1. Busca pura
# ---------------------------------------------------------------------------

def retrieve(
    query: str,
    limit: int = DEFAULT_LIMIT,
    collection_name: str = DEFAULT_COLLECTION,
    act_type: str | None = None,
    source_file: str | None = None,
) -> list[dict]:
    """Busca os chunks mais relevantes para a pergunta. Não chama LLM."""
    embedder = _get_embedder()
    indexer = _get_indexer(collection_name)
    query_vector = embedder.embed_query(query)
    return indexer.search(
        query_vector,
        limit=limit,
        act_type=act_type,
        source_file=source_file,
    )


# ---------------------------------------------------------------------------
# 2. Tool para function calling
# ---------------------------------------------------------------------------

definition = {
    "type": "function",
    "function": {
        "name": "consultar_atos_sejus",
        "description": (
            "Busca nos atos administrativos da SEJUS (Instruções Normativas, "
            "Portarias, Decretos, Editais etc.) e retorna os trechos relevantes "
            "para responder perguntas sobre requisitos, prazos e procedimentos. "
            "Use esta ferramenta sempre que a pergunta depender de regras "
            "internas da SEJUS."
        ),
        "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A pergunta do usuário, em português, tal como formulada.",
            },
            "act_type": {
                "type": "string",
                "description": (
                    "Opcional. Filtra por tipo de ato, se o usuário mencionar "
                    "um explicitamente (ex: 'INSTRUÇÃO NORMATIVA', 'PORTARIA', "
                    "'DECRETO', 'PORTARIA CONJUNTA')."
                ),
            },
        },
        "required": ["query"],
        },
    },
}

# Mantém um alias curto para integrações que importavam o schema antigo.
TOOL_SCHEMA = {
    "name": definition["function"]["name"],
    "description": definition["function"]["description"],
    "input_schema": definition["function"]["parameters"],
}


def handle_tool_call(tool_input: dict) -> dict:
    """Executa a busca e devolve os resultados serializáveis."""
    query = tool_input["query"]
    act_type = tool_input.get("act_type")
    return {
        "results": [
            {
                "act_type": source.get("act_type"),
                "act_number": source.get("act_number"),
                "source_file": source.get("source_file"),
                "score": source.get("score"),
                "text": source.get("text", ""),
            }
            for source in retrieve(query, act_type=act_type)
        ]
    }


def consultar_atos_sejus(
    query: str,
    act_type: str | None = None,
) -> str:
    """Busca trechos para o agente e devolve conteúdo JSON serializável."""
    return json.dumps(
        handle_tool_call({"query": query, "act_type": act_type}),
        ensure_ascii=False,
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Uso: python retrieval.py \"sua pergunta aqui\"")
        sys.exit(1)

    question = sys.argv[1]
    result = retrieve(question)

    for source in result:
        print(
            f"- {source.get('act_type')} {source.get('act_number')} "
            f"({source.get('source_file')}) score={source.get('score'):.3f}\n"
            f"  {source.get('text', '')}\n"
        )