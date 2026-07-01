"""OpenRouter client."""

from __future__ import annotations

import base64
import json
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from app.config import Settings


class OpenRouterError(RuntimeError):
    """Base OpenRouter client error."""


class OpenRouterAuthError(OpenRouterError):
    """Raised when OpenRouter rejects credentials."""


class OpenRouterBadRequestError(OpenRouterError):
    """Raised when OpenRouter rejects a request or model id."""


class OpenRouterRateLimitError(OpenRouterError):
    """Raised when OpenRouter returns a rate-limit response."""


@dataclass(frozen=True)
class OpenRouterModel:
    """Small model metadata shape returned by `/models`."""

    id: str
    name: str
    context_length: int
    input_modalities: tuple[str, ...]
    output_modalities: tuple[str, ...]


class OpenRouterClient:
    """Async OpenRouter-compatible chat client."""

    def __init__(self, settings: "Settings") -> None:
        self._api_key = settings.openrouter_api_key.strip()
        self._model = settings.openrouter_model
        self._site_url = settings.openrouter_site_url.strip()
        self._app_name = settings.openrouter_app_name.strip() or "AI Kurator V2"
        self._client = httpx.AsyncClient(
            base_url=settings.openrouter_base_url.rstrip("/"),
            timeout=60.0,
            trust_env=False,
        )

    async def list_models(self, output_modalities: str = "text", free_only: bool = True) -> list[OpenRouterModel]:
        """List suitable OpenRouter chat models."""
        response = await self._client.get(
            "/models",
            params={"output_modalities": output_modalities, "sort": "pricing-low-to-high"},
            headers=self._headers(require_auth=False),
        )
        _raise_for_openrouter(response, model="<models>")

        models: list[OpenRouterModel] = []
        for item in response.json().get("data") or []:
            pricing = item.get("pricing") or {}
            if free_only and not _is_free(pricing):
                continue
            architecture = item.get("architecture") or {}
            input_modalities = tuple(architecture.get("input_modalities") or ())
            output_modalities_list = tuple(architecture.get("output_modalities") or ())
            if output_modalities == "text" and not _is_text_chat_model(input_modalities, output_modalities_list):
                continue
            model_id = str(item.get("id") or "")
            model_name = str(item.get("name") or model_id)
            if output_modalities == "text" and not _is_suitable_assistant_model(model_id, model_name):
                continue
            if not model_id:
                continue
            models.append(
                OpenRouterModel(
                    id=model_id,
                    name=model_name,
                    context_length=int(item.get("context_length") or 0),
                    input_modalities=input_modalities,
                    output_modalities=output_modalities_list,
                )
            )
        return sorted(models, key=_model_sort_key)

    async def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """Request a JSON object from OpenRouter."""
        if not self._api_key:
            raise OpenRouterAuthError("OPENROUTER_API_KEY is required for LLM document cards")

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
            raise OpenRouterAuthError("OPENROUTER_API_KEY is required for answer generation")

        return await self.complete_text_with_model(self._model, messages)

    async def complete_text_with_model(self, model: str, messages: list[dict[str, str]]) -> str:
        """Request a text completion from a specific model."""
        if not self._api_key:
            raise OpenRouterAuthError("OPENROUTER_API_KEY is required for answer generation")

        response = await self._post_chat(
            {
                "model": model,
                "messages": messages,
                "temperature": 0.1,
            },
            model=model,
        )
        return _message_content(response, model=model)

    async def complete_vision_with_model(self, model: str, image_payload: object, prompt: str) -> str:
        """Request a vision completion from a specific model."""
        if not self._api_key:
            raise OpenRouterAuthError("OPENROUTER_API_KEY is required for vision")

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
            },
            model=model,
        )
        return _message_content(response, model=model)

    async def _post_chat(self, payload: dict[str, Any], model: str | None = None) -> httpx.Response:
        response = await self._client.post(
            "/chat/completions",
            headers=self._headers(require_auth=True),
            json=payload,
        )
        _raise_for_openrouter(response, model=model or str(payload.get("model") or "unknown"))
        return response

    def _headers(self, *, require_auth: bool) -> dict[str, str]:
        if require_auth and not self._api_key:
            raise OpenRouterAuthError("OPENROUTER_API_KEY is required")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if self._site_url:
            headers["HTTP-Referer"] = self._site_url
        if self._app_name:
            headers["X-Title"] = self._app_name
        return headers

    async def close(self) -> None:
        """Close underlying HTTP resources."""
        await self._client.aclose()


def _message_content(response: httpx.Response, *, model: str) -> str:
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise OpenRouterError(f"OpenRouter returned no choices for model {model}: {data}")
    content = str((choices[0].get("message") or {}).get("content") or "").strip()
    if looks_like_bad_output(content):
        raise OpenRouterError(f"OpenRouter model {model} returned a service response instead of an answer")
    return content


def _raise_for_openrouter(response: httpx.Response, *, model: str) -> None:
    if response.status_code in {401, 403}:
        raise OpenRouterAuthError("OpenRouter authorization failed. Check OPENROUTER_API_KEY.")
    if response.status_code == 400:
        raise OpenRouterBadRequestError(
            f"OpenRouter request failed for model {model}: 400 {_sanitize_error_body(response.text)}"
        )
    if response.status_code == 429:
        raise OpenRouterRateLimitError("OpenRouter rate limit exceeded.")
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise OpenRouterError(
            f"OpenRouter request failed for model {model}: "
            f"{exc.response.status_code} {_sanitize_error_body(exc.response.text)}"
        ) from exc


def looks_like_bad_output(answer: str) -> bool:
    """Return true for service/safety artifacts instead of useful model text."""
    normalized = " ".join(answer.strip().lower().split())
    if not normalized:
        return True
    bad_prefixes = (
        "user safety:",
        "safety:",
        "content safety:",
        "safe",
        "unsafe",
    )
    if any(normalized.startswith(prefix) for prefix in bad_prefixes) and len(normalized) < 300:
        return True
    return normalized in {"user safety: safe", "safe", "unsafe"}


def _sanitize_error_body(body: str) -> str:
    clean = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer <redacted>", body)
    clean = re.sub(r"sk-or-v1-[A-Za-z0-9_-]+", "sk-or-v1-<redacted>", clean)
    clean = re.sub(r"bot[0-9]{6,}(?::|%3[Aa])[A-Za-z0-9_-]+", "bot<redacted>", clean)
    clean = re.sub(r"sb_secret_[A-Za-z0-9_-]+", "sb_secret_<redacted>", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:500]


def _image_payload_to_data_url(image_payload: object) -> str:
    if isinstance(image_payload, str) and image_payload.startswith("data:"):
        return image_payload
    if isinstance(image_payload, Path):
        path = image_payload
    else:
        path = Path(str(image_payload))
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _is_free(pricing: dict[str, Any]) -> bool:
    keys = ("prompt", "completion", "request", "image", "web_search", "internal_reasoning")
    for key in keys:
        try:
            if float(pricing.get(key) or 0) != 0:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _is_text_chat_model(input_modalities: tuple[str, ...], output_modalities: tuple[str, ...]) -> bool:
    return "text" in input_modalities and output_modalities == ("text",)


def _is_suitable_assistant_model(model_id: str, model_name: str) -> bool:
    text = f"{model_id} {model_name}".lower()
    blocked = {
        "safety",
        "guard",
        "moderation",
        "content-safety",
        "embed",
        "embedding",
        "lyria",
        "clip-preview",
    }
    return not any(word in text for word in blocked)


def _model_sort_key(model: OpenRouterModel) -> tuple[int, int, str]:
    model_id = model.id.lower()
    preferred_order = (
        "qwen/",
        "openai/",
        "google/",
        "liquid/",
        "poolside/",
        "nvidia/",
        "mistralai/",
        "meta-llama/",
        "openrouter/",
    )
    for index, prefix in enumerate(preferred_order):
        if model_id.startswith(prefix):
            return (index, -model.context_length, model.id)
    return (len(preferred_order), -model.context_length, model.id)
