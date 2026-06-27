"""OpenRouter client."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from app.config import Settings


class OpenRouterClient:
    """Async OpenRouter-compatible chat client."""

    def __init__(self, settings: "Settings") -> None:
        self._api_key = settings.openrouter_api_key
        self._model = settings.openrouter_model
        self._client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            timeout=60.0,
            trust_env=False,
        )

    async def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """Request a JSON object from OpenRouter."""
        if not self._api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required for LLM document cards")

        response = await self._client.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)

    async def complete_text(self, messages: list[dict[str, str]]) -> str:
        """Request a text completion from OpenRouter."""
        if not self._api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required for answer generation")

        response = await self._client.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "messages": messages,
                "temperature": 0.1,
            },
        )
        response.raise_for_status()
        return str(response.json()["choices"][0]["message"]["content"])

    async def close(self) -> None:
        """Close underlying HTTP resources."""
        await self._client.aclose()
