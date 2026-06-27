"""Local embedding clients."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import httpx

if TYPE_CHECKING:
    from app.config import Settings


class EmbeddingClient(Protocol):
    """Embedding adapter protocol."""

    async def embed(self, text: str) -> list[float]:
        """Embed one text string."""

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Embed many text strings."""


class OllamaEmbeddingClient:
    """Generate local embeddings through Ollama."""

    def __init__(self, settings: "Settings") -> None:
        self._model = settings.embedding_model or settings.ollama_embedding_model
        self._expected_dim = settings.embedding_dim
        self._client = httpx.AsyncClient(
            base_url=settings.ollama_base_url.rstrip("/"),
            timeout=60.0,
            trust_env=False,
        )

    async def embed(self, text: str) -> list[float]:
        """Embed one text string."""
        return (await self.embed_many([text]))[0]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Embed many text strings through Ollama."""
        if not texts:
            return []

        try:
            response = await self._client.post(
                "/api/embed",
                json={"model": self._model, "input": texts},
            )
            response.raise_for_status()
            data = response.json()
            embeddings = data.get("embeddings")
            if isinstance(embeddings, list):
                return [self._validate(vector) for vector in embeddings]
        except httpx.HTTPError:
            if len(texts) != 1:
                return [await self.embed(text) for text in texts]

        if len(texts) == 1:
            response = await self._client.post(
                "/api/embeddings",
                json={"model": self._model, "prompt": texts[0]},
            )
            response.raise_for_status()
            data = response.json()
            embedding = data.get("embedding")
            if isinstance(embedding, list):
                return [self._validate(embedding)]

        raise RuntimeError("Ollama did not return embeddings")

    async def close(self) -> None:
        """Close underlying HTTP resources."""
        await self._client.aclose()

    def _validate(self, embedding: list[float]) -> list[float]:
        vector = [float(value) for value in embedding]
        if self._expected_dim and len(vector) != self._expected_dim:
            raise RuntimeError(
                f"Embedding dimension mismatch: expected {self._expected_dim}, got {len(vector)}"
            )
        return vector
