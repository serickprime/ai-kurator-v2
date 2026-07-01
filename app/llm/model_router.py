"""User-mode aware OpenRouter model routing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.config import Settings


AnswerModeName = str
ABSTRACT_MODEL_IDS = frozenset(
    {
        "openrouter/free",
        "openrouter/auto",
        "auto",
        "free",
        "cheap",
        "quality",
    }
)


class RoutedModelClient(Protocol):
    """Minimal client interface used by the model router."""

    async def complete_text_with_model(self, model: str, messages: list[dict[str, str]]) -> str:
        """Return a text completion for a specific model."""

    async def complete_vision_with_model(self, model: str, image_payload: object, prompt: str) -> str:
        """Return a vision completion for a specific model."""


@dataclass(frozen=True)
class ModelAttempt:
    """One model attempt in a fallback chain."""

    model: str
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class ModelRoutingMetadata:
    """Debug metadata for model fallback diagnostics."""

    requested_mode: str
    attempted_models: tuple[str, ...] = ()
    failed_models: tuple[str, ...] = ()
    successful_model: str | None = None
    provider_errors: tuple[str, ...] = ()
    degraded_quality: bool = False


@dataclass(frozen=True)
class ModelRouterResult:
    """Successful routed model result."""

    text: str
    metadata: ModelRoutingMetadata


class ModelRouterError(RuntimeError):
    """Raised when every model in a route fails."""

    def __init__(self, user_message: str, metadata: ModelRoutingMetadata) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.metadata = metadata


@dataclass(frozen=True)
class ModelRouterConfig:
    """Model lists grouped by cost/quality mode."""

    free_text: tuple[str, ...] = ()
    free_vision: tuple[str, ...] = ()
    cheap_text: tuple[str, ...] = ()
    cheap_vision: tuple[str, ...] = ()
    quality_text: tuple[str, ...] = ()
    quality_vision: tuple[str, ...] = ()
    allow_quality_to_cheap_fallback: bool = False

    @classmethod
    def from_settings(cls, settings: "Settings") -> "ModelRouterConfig":
        """Build routing config from environment-backed settings."""
        return cls(
            free_text=_concrete_models(settings.openrouter_free_text_models),
            free_vision=_concrete_models(settings.openrouter_free_vision_models),
            cheap_text=_concrete_models(settings.openrouter_cheap_text_models or settings.openrouter_model),
            cheap_vision=_concrete_models(settings.openrouter_cheap_vision_models or settings.vision_model),
            quality_text=_concrete_models(settings.openrouter_quality_text_models or settings.openrouter_model),
            quality_vision=_concrete_models(settings.openrouter_quality_vision_models or settings.vision_model),
            allow_quality_to_cheap_fallback=settings.allow_quality_to_cheap_fallback,
        )


class ModelRouter:
    """Try OpenRouter models according to the user's selected mode."""

    def __init__(self, client: RoutedModelClient, config: ModelRouterConfig) -> None:
        self._client = client
        self._config = config

    async def complete_text(self, messages: list[dict[str, str]], answer_mode: str) -> ModelRouterResult:
        """Complete text with fallbacks for one user mode."""
        return await self._run_text_route(messages, answer_mode=answer_mode)

    async def complete_vision(self, image_payload: object, prompt: str, answer_mode: str) -> ModelRouterResult:
        """Complete vision with fallbacks for one user mode."""
        return await self._run_vision_route(image_payload, prompt, answer_mode=answer_mode)

    async def _run_text_route(self, messages: list[dict[str, str]], answer_mode: str) -> ModelRouterResult:
        models, degraded = self._models_for(answer_mode, kind="text")
        attempts: list[ModelAttempt] = []
        for model in models:
            try:
                text = await self._client.complete_text_with_model(model, messages)
                attempts.append(ModelAttempt(model=model, ok=True))
                return ModelRouterResult(
                    text=text,
                    metadata=_metadata(answer_mode, attempts, degraded_quality=degraded),
                )
            except Exception as exc:  # noqa: BLE001 - provider failures are expected in fallback chains
                attempts.append(ModelAttempt(model=model, ok=False, error=str(exc)))
        raise ModelRouterError(
            _failure_message(answer_mode),
            _metadata(answer_mode, attempts, degraded_quality=degraded),
        )

    async def _run_vision_route(self, image_payload: object, prompt: str, answer_mode: str) -> ModelRouterResult:
        models, degraded = self._models_for(answer_mode, kind="vision")
        attempts: list[ModelAttempt] = []
        for model in models:
            try:
                text = await self._client.complete_vision_with_model(model, image_payload, prompt)
                attempts.append(ModelAttempt(model=model, ok=True))
                return ModelRouterResult(
                    text=text,
                    metadata=_metadata(answer_mode, attempts, degraded_quality=degraded),
                )
            except Exception as exc:  # noqa: BLE001
                attempts.append(ModelAttempt(model=model, ok=False, error=str(exc)))
        raise ModelRouterError(
            _failure_message(answer_mode),
            _metadata(answer_mode, attempts, degraded_quality=degraded),
        )

    def _models_for(self, answer_mode: str, kind: str) -> tuple[tuple[str, ...], bool]:
        mode = answer_mode if answer_mode in {"free", "cheap", "quality"} else "cheap"
        if mode == "free":
            return (_models_or_empty(self._config.free_text if kind == "text" else self._config.free_vision), False)
        if mode == "quality":
            primary = self._config.quality_text if kind == "text" else self._config.quality_vision
            if self._config.allow_quality_to_cheap_fallback:
                fallback = self._config.cheap_text if kind == "text" else self._config.cheap_vision
                return (_models_or_empty(primary + fallback), bool(fallback))
            return (_models_or_empty(primary), False)
        models = self._config.cheap_text if kind == "text" else self._config.cheap_vision
        return (_models_or_empty(models), False)


class ModelRoutedAnswerClient:
    """AnswerGenerator-compatible client that reads answer mode from dialog context."""

    def __init__(self, router: ModelRouter, default_answer_mode: str = "cheap") -> None:
        self._router = router
        self._default_answer_mode = default_answer_mode
        self.last_metadata: ModelRoutingMetadata | None = None

    async def complete_text(self, messages: list[dict[str, str]]) -> str:
        """Complete text with the default mode."""
        result = await self._complete_text_with_metadata(messages, self._default_answer_mode)
        return result.text

    async def complete_text_for_dialog(
        self,
        messages: list[dict[str, str]],
        dialog_context: object | None,
    ) -> str:
        """Complete text using the per-user mode stored in dialog context."""
        result = await self._complete_text_with_metadata(
            messages,
            _answer_mode_from_dialog_context(dialog_context, self._default_answer_mode),
        )
        return result.text

    async def _complete_text_with_metadata(
        self,
        messages: list[dict[str, str]],
        answer_mode: str,
    ) -> ModelRouterResult:
        try:
            result = await self._router.complete_text(messages, answer_mode)
        except ModelRouterError as exc:
            self.last_metadata = exc.metadata
            raise
        self.last_metadata = result.metadata
        return result


def _split_models(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _concrete_models(value: str) -> tuple[str, ...]:
    return tuple(model for model in _split_models(value) if is_concrete_model_id(model))


def is_concrete_model_id(model: str) -> bool:
    """Return true when a configured model looks like a concrete OpenRouter model id."""
    clean = model.strip().lower()
    if not clean or clean in ABSTRACT_MODEL_IDS:
        return False
    return bool(re.match(r"^[a-z0-9_.-]+/[a-z0-9_.:-]+$", clean))


def _models_or_empty(models: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(models))


def _metadata(
    requested_mode: str,
    attempts: list[ModelAttempt],
    *,
    degraded_quality: bool,
) -> ModelRoutingMetadata:
    return ModelRoutingMetadata(
        requested_mode=requested_mode,
        attempted_models=tuple(attempt.model for attempt in attempts),
        failed_models=tuple(attempt.model for attempt in attempts if not attempt.ok),
        successful_model=next((attempt.model for attempt in attempts if attempt.ok), None),
        provider_errors=tuple(_sanitize_error(attempt.error or "") for attempt in attempts if attempt.error),
        degraded_quality=degraded_quality,
    )


def _failure_message(answer_mode: str) -> str:
    if answer_mode == "free":
        return (
            "Бесплатные модели сейчас не ответили. "
            "Можно попробовать ещё раз позже или переключить режим в настройках на Дешево."
        )
    return "Модели сейчас не ответили. Попробуйте ещё раз позже или выберите другой режим в настройках."


def _answer_mode_from_dialog_context(dialog_context: object | None, default: str) -> str:
    if isinstance(dialog_context, dict):
        settings = dialog_context.get("user_settings")
        if isinstance(settings, dict):
            mode = str(settings.get("answer_mode") or default)
            if mode in {"free", "cheap", "quality"}:
                return mode
    return default


def _sanitize_error(error: str) -> str:
    clean = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer <redacted>", str(error))
    clean = re.sub(r"bot[0-9]{6,}(?::|%3[Aa])[A-Za-z0-9_-]+", "bot<redacted>", clean)
    clean = re.sub(r"sb_secret_[A-Za-z0-9_-]+", "sb_secret_<redacted>", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:500]
