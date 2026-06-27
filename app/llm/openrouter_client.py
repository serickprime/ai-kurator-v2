"""OpenRouter client placeholder."""

import httpx

from app.config import Settings


class OpenRouterClient:
    """Async OpenRouter-compatible chat client."""

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.openrouter_api_key
        self._model = settings.openrouter_model
        self._client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            timeout=60.0,
            trust_env=False,
        )

    async def close(self) -> None:
        """Close underlying HTTP resources."""
        await self._client.aclose()
