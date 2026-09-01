"""
embedding.py
------------
Gera embeddings para os chunks usando sentence-transformers, rodando
localmente (sem precisar de API key).

Modelo padrão: intfloat/multilingual-e5-large
  - Multilíngue, bom desempenho em português
  - Dimensão do vetor: 1024
  - Requer prefixar o texto com "query: " (para buscas) ou "passage: "
    (para documentos) -- é a convenção do e5, melhora a qualidade do
    retrieval. Isso já é tratado nas funções abaixo.

Se quiser algo mais leve/rápido (CPU sem GPU, por exemplo), troque por:
  "paraphrase-multilingual-mpnet-base-v2" (dimensão 768, sem necessidade
  de prefixo "query:"/"passage:").

Uso:
    from embedding import Embedder

    embedder = Embedder()
    vectors = embedder.embed_passages([c.text for c in chunks])
    query_vector = embedder.embed_query("qual o prazo do grupo de trabalho?")
"""
from __future__ import annotations

from dataclasses import dataclass

from sentence_transformers import SentenceTransformer

DEFAULT_MODEL = "intfloat/multilingual-e5-large"

# Modelos que seguem a convenção de prefixo "query:"/"passage:" (família e5/bge)
_E5_STYLE_MODELS = ("e5", "bge")


@dataclass
class EmbeddingConfig:
    model_name: str = DEFAULT_MODEL
    batch_size: int = 16
    normalize: bool = True  # normalização L2 -- necessária para usar
    # distância "cosine" no Qdrant com bons resultados


class Embedder:
    def __init__(self, config: EmbeddingConfig | None = None):
        self.config = config or EmbeddingConfig()
        self.model = SentenceTransformer(self.config.model_name)
        self._uses_prefix = any(k in self.config.model_name.lower() for k in _E5_STYLE_MODELS)

    @property
    def dimension(self) -> int:
        return self.model.get_sentence_embedding_dimension()

    def _prep(self, texts: list[str], is_query: bool) -> list[str]:
        if not self._uses_prefix:
            return texts
        prefix = "query: " if is_query else "passage: "
        return [prefix + t for t in texts]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """Gera embeddings para chunks/documentos (o que vai para o índice)."""
        prepped = self._prep(texts, is_query=False)
        vectors = self.model.encode(
            prepped,
            batch_size=self.config.batch_size,
            normalize_embeddings=self.config.normalize,
            show_progress_bar=True,
        )
        return vectors.tolist()

    def embed_query(self, text: str) -> list[float]:
        """Gera o embedding de uma pergunta/consulta de busca."""
        prepped = self._prep([text], is_query=True)
        vector = self.model.encode(
            prepped,
            normalize_embeddings=self.config.normalize,
        )
        return vector[0].tolist()


if __name__ == "__main__":
    embedder = Embedder()
    print(f"Modelo: {embedder.config.model_name}")
    print(f"Dimensão: {embedder.dimension}")
    sample = embedder.embed_passages(["Art. 1º Fica instituído o Grupo de Trabalho."])
    print(f"Exemplo de vetor (primeiros 5 valores): {sample[0][:5]}")