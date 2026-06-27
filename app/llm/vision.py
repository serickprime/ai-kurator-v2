"""Vision model adapters."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

import httpx

from app.config import Settings


class VisionTextifier:
    """Extract text from screenshots and images with an OpenRouter vision model."""

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.openrouter_api_key
        self._model = settings.vision_model
        self._client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            timeout=90.0,
            trust_env=False,
        )

    async def describe_image(self, path: Path) -> str:
        """Return a compact description of an image for indexing."""
        if not self._api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required for vision ingestion")

        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        data_url = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        response = await self._client.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Describe the educational or technical content in this image. "
                                    "Focus on visible UI, diagrams, errors, commands, settings, and labels."
                                ),
                            },
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
                "temperature": 0.1,
            },
        )
        response.raise_for_status()
        return str(response.json()["choices"][0]["message"]["content"]).strip()

    async def textify_image(self, path: Path) -> str:
        """Return text extracted from an image."""
        return await self.describe_image(path)

    async def close(self) -> None:
        """Close underlying HTTP resources."""
        await self._client.aclose()
