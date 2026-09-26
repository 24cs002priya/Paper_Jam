import asyncio
from functools import cached_property
from typing import Protocol

from sentence_transformers import SentenceTransformer

from app.core.config import get_settings


class EmbeddingProvider(Protocol):
    @property
    def dimension(self) -> int: ...
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_text(self, text: str) -> list[float]: ...


class SentenceTransformerEmbeddingService:
    def __init__(self) -> None:
        self.model_name = get_settings().embedding_model
        self._model: SentenceTransformer | None = None

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            self._model = SentenceTransformer(self.model_name)
        return self._model

    @property
    def dimension(self) -> int:
        value = self.model.get_sentence_embedding_dimension()
        if value is None:
            raise RuntimeError("Embedding model did not report its output dimension")
        return int(value)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        batch_size = get_settings().embedding_batch_size
        vectors = await asyncio.to_thread(self.model.encode, texts, batch_size=batch_size, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        return vectors.astype(float).tolist()

    async def embed_text(self, text: str) -> list[float]:
        result = await self.embed_documents([text])
        return result[0]


embedding_service = SentenceTransformerEmbeddingService()
