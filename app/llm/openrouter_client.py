"""OpenRouter client."""

from __future__ import annotations

import json
import base64
import mimetypes
from pathlib import Path
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

        response = await self._post_chat(
            {
                "model": self._model,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
            }
        )
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)

    async def complete_text(self, messages: list[dict[str, str]]) -> str:
        """Request a text completion from OpenRouter."""
        if not self._api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required for answer generation")

        return await self.complete_text_with_model(self._model, messages)

    async def complete_text_with_model(self, model: str, messages: list[dict[str, str]]) -> str:
        """Request a text completion from a specific model."""
        if not self._api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required for answer generation")

        response = await self._post_chat(
            {
                "model": model,
                "messages": messages,
                "temperature": 0.1,
            }
        )
        return str(response.json()["choices"][0]["message"]["content"])

    async def complete_vision_with_model(self, model: str, image_payload: object, prompt: str) -> str:
        """Request a vision completion from a specific model."""
        if not self._api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required for vision")

        data_url = _image_payload_to_data_url(image_payload)
        response = await self._post_chat(
            {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
                "temperature": 0.1,
            }
        )
        return str(response.json()["choices"][0]["message"]["content"])

    async def _post_chat(self, payload: dict[str, Any]) -> httpx.Response:
        response = await self._client.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=payload,
        )
        response.raise_for_status()
        return response

    async def close(self) -> None:
        """Close underlying HTTP resources."""
        await self._client.aclose()


def _image_payload_to_data_url(image_payload: object) -> str:
    if isinstance(image_payload, str) and image_payload.startswith("data:"):
        return image_payload
    if isinstance(image_payload, Path):
        path = image_payload
    else:
        path = Path(str(image_payload))
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
