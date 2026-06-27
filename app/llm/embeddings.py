"""Local embedding client placeholders."""

import httpx

from app.config import Settings


class OllamaEmbeddingClient:
    """Generate local embeddings through Ollama."""

    def __init__(self, settings: Settings) -> None:
        self._model = settings.ollama_embedding_model
        self._client = httpx.AsyncClient(
            base_url=settings.ollama_base_url.rstrip("/"),
            timeout=60.0,
            trust_env=False,
        )

    async def embed(self, text: str) -> list[float]:
        """Embed one text string."""
        del text
        raise NotImplementedError("Embedding generation is not implemented yet")

    async def close(self) -> None:
        """Close underlying HTTP resources."""
        await self._client.aclose()
