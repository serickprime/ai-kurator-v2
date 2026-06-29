"""Smoke-check OpenRouter model routing config without printing secrets."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.llm.model_router import ModelRouterConfig, is_concrete_model_id  # noqa: E402
from app.llm.openrouter_client import OpenRouterClient, OpenRouterError  # noqa: E402


async def main_async() -> int:
    """Validate configured model ids and run one tiny completion."""
    settings = get_settings()
    if not settings.openrouter_api_key or settings.openrouter_api_key.startswith("replace_with"):
        print("OpenRouter models: disabled")
        print("Missing: OPENROUTER_API_KEY")
        return 2

    raw = _raw_models(settings)
    invalid = [model for model in raw if model and not is_concrete_model_id(model)]
    if invalid:
        print("OpenRouter models: invalid config")
        print("Abstract or invalid model ids cannot be used for generation:")
        for model in invalid:
            print(f"- {model}")
        print("Use concrete ids, for example provider/model-name, in .env model lists.")
        return 2

    config = ModelRouterConfig.from_settings(settings)
    candidate = _first_model(config)
    if not candidate:
        print("OpenRouter models: invalid config")
        print("No concrete text model is configured for cheap, quality, or free mode.")
        return 2

    client = OpenRouterClient(settings)
    try:
        text = await client.complete_text_with_model(
            candidate,
            [{"role": "user", "content": "Reply with exactly: OK"}],
        )
    except OpenRouterError as exc:
        print("OpenRouter models: failed")
        print(str(exc))
        return 1
    finally:
        await client.close()

    print("OpenRouter models: ready")
    print(f"tested_model={candidate}")
    print(f"response={text[:60]}")
    print(f"free_models={len(config.free_text)} cheap_models={len(config.cheap_text)} quality_models={len(config.quality_text)}")
    return 0


def _raw_models(settings: object) -> list[str]:
    values = [
        getattr(settings, "openrouter_default_model", ""),
        getattr(settings, "openrouter_free_text_models", ""),
        getattr(settings, "openrouter_cheap_text_models", ""),
        getattr(settings, "openrouter_quality_text_models", ""),
    ]
    result: list[str] = []
    for value in values:
        result.extend(item.strip() for item in str(value).split(",") if item.strip())
    return result


def _first_model(config: ModelRouterConfig) -> str:
    for models in (config.cheap_text, config.quality_text, config.free_text):
        if models:
            return models[0]
    return ""


def main() -> None:
    """CLI entry point."""
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
