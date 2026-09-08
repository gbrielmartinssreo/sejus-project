from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

DEFAULT_MODEL = "text-embedding-3-small"


@dataclass
class EmbeddingConfig:
    model_name: str = DEFAULT_MODEL
    batch_size: int = 100


class Embedder:
    def __init__(self, config: EmbeddingConfig | None = None):
        self.config = config or EmbeddingConfig()

        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY não encontrada no ambiente."
            )

        self.client = OpenAI(api_key=api_key)

    @property
    def dimension(self) -> int:
        return 1536

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        total = len(texts)

        for start in range(0, total, self.config.batch_size):
            batch = texts[start:start + self.config.batch_size]

            response = self.client.embeddings.create(
                model=self.config.model_name,
                input=batch,
            )

            # Mantém a ordem original dos textos
            data = sorted(response.data, key=lambda x: x.index)

            vectors.extend(item.embedding for item in data)

            processed = min(
                start + self.config.batch_size,
                total
            )

            print(f"  embeddings: {processed}/{total}")

        return vectors

    def embed_query(self, text: str) -> list[float]:
        response = self.client.embeddings.create(
            model=self.config.model_name,
            input=text,
        )

        return response.data[0].embedding
