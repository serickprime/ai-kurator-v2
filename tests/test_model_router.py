import asyncio

import pytest

from app.config import Settings
from app.llm.model_router import (
    ModelRoutedAnswerClient,
    ModelRouter,
    ModelRouterConfig,
    ModelRouterError,
)


class FakeRoutedClient:
    def __init__(self, responses: dict[str, str | Exception]) -> None:
        self.responses = responses
        self.text_attempts: list[str] = []
        self.vision_attempts: list[str] = []

    async def complete_text_with_model(self, model: str, messages: list[dict[str, str]]) -> str:
        del messages
        self.text_attempts.append(model)
        response = self.responses[model]
        if isinstance(response, Exception):
            raise response
        return response

    async def complete_vision_with_model(self, model: str, image_payload: object, prompt: str) -> str:
        del image_payload, prompt
        self.vision_attempts.append(model)
        response = self.responses[model]
        if isinstance(response, Exception):
            raise response
        return response


def test_free_mode_tries_only_free_models() -> None:
    client = FakeRoutedClient(
        {
            "free-a": RuntimeError("quota"),
            "free-b": RuntimeError("down"),
            "cheap-a": "paid answer",
        }
    )
    router = ModelRouter(
        client,
        ModelRouterConfig(
            free_text=("free-a", "free-b"),
            cheap_text=("cheap-a",),
        ),
    )

    with pytest.raises(ModelRouterError) as exc:
        asyncio.run(router.complete_text([], "free"))

    assert client.text_attempts == ["free-a", "free-b"]
    assert "Бесплатные модели" in exc.value.user_message


def test_quality_to_cheap_fallback_requires_flag() -> None:
    client = FakeRoutedClient({"quality-a": RuntimeError("down"), "cheap-a": "cheap answer"})
    router = ModelRouter(
        client,
        ModelRouterConfig(
            quality_text=("quality-a",),
            cheap_text=("cheap-a",),
            allow_quality_to_cheap_fallback=False,
        ),
    )

    with pytest.raises(ModelRouterError):
        asyncio.run(router.complete_text([], "quality"))

    assert client.text_attempts == ["quality-a"]


def test_quality_to_cheap_fallback_when_enabled() -> None:
    client = FakeRoutedClient({"quality-a": RuntimeError("down"), "cheap-a": "cheap answer"})
    router = ModelRouter(
        client,
        ModelRouterConfig(
            quality_text=("quality-a",),
            cheap_text=("cheap-a",),
            allow_quality_to_cheap_fallback=True,
        ),
    )

    result = asyncio.run(router.complete_text([], "quality"))

    assert result.text == "cheap answer"
    assert client.text_attempts == ["quality-a", "cheap-a"]
    assert result.metadata.degraded_quality is True


def test_answer_client_reads_mode_from_dialog_context() -> None:
    client = FakeRoutedClient({"free-a": "free answer", "cheap-a": "cheap answer"})
    router = ModelRouter(
        client,
        ModelRouterConfig(
            free_text=("free-a",),
            cheap_text=("cheap-a",),
        ),
    )
    answer_client = ModelRoutedAnswerClient(router)

    text = asyncio.run(
        answer_client.complete_text_for_dialog(
            [],
            {"user_settings": {"answer_mode": "free"}},
        )
    )

    assert text == "free answer"
    assert client.text_attempts == ["free-a"]


def test_abstract_openrouter_model_id_is_not_used_as_concrete_model() -> None:
    settings = Settings(
        _env_file=None,
        openrouter_default_model="openrouter/free",
        openrouter_cheap_text_models="openai/gpt-4.1-mini,openrouter/free",
    )

    config = ModelRouterConfig.from_settings(settings)

    assert config.cheap_text == ("openai/gpt-4.1-mini",)


def test_router_sanitizes_provider_errors_and_tries_next_model() -> None:
    client = FakeRoutedClient(
        {
            "cheap-a": RuntimeError("400 bad request Authorization: Bearer secret-token"),
            "cheap-b": "safe answer",
        }
    )
    router = ModelRouter(client, ModelRouterConfig(cheap_text=("cheap-a", "cheap-b")))

    result = asyncio.run(router.complete_text([], "cheap"))

    assert result.text == "safe answer"
    assert client.text_attempts == ["cheap-a", "cheap-b"]
    assert "secret-token" not in " ".join(result.metadata.provider_errors)
    assert "Bearer <redacted>" in " ".join(result.metadata.provider_errors)
