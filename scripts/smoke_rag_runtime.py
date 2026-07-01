"""Smoke-check RAG v2 runtime wiring without starting Telegram polling."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.rag.runtime import build_rag_runtime_from_settings, validate_runtime_config  # noqa: E402


async def main_async() -> int:
    """Build the RAG runtime or print missing configuration."""
    settings = _load_settings()
    validation = validate_runtime_config(settings)
    if not validation.ready:
        print("RAG runtime: disabled")
        print("Missing:")
        for item in validation.missing:
            print(f"- {item}")
        if validation.warnings:
            print("Warnings:")
            for item in validation.warnings:
                print(f"- {item}")
        return 0

    runtime = build_rag_runtime_from_settings(settings)
    if runtime is None:
        print("RAG runtime: disabled")
        print("Pipeline builder returned None. Check logs for details.")
        return 0

    try:
        pipeline = runtime.pipeline
        print("RAG runtime: ready")
        print(f"Pipeline: {pipeline.__class__.__name__}")
        print(f"Retriever: {pipeline._retriever.__class__.__name__}")  # noqa: SLF001 - smoke diagnostic
        print(f"AnswerGenerator: {pipeline._answer_generator.__class__.__name__}")  # noqa: SLF001
        print(f"Workspace: {settings.default_workspace_id}")
        return 0
    finally:
        await runtime.close()


def main() -> None:
    """CLI entry point."""
    raise SystemExit(asyncio.run(main_async()))


def _load_settings() -> object:
    try:
        from app.config import get_settings

        return get_settings()
    except ModuleNotFoundError as exc:
        if exc.name != "pydantic_settings":
            raise
        return SimpleNamespace(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            supabase_url=os.getenv("SUPABASE_URL", ""),
            supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
            default_workspace_id=os.getenv("DEFAULT_WORKSPACE_ID", ""),
            default_workspace_name=os.getenv("DEFAULT_WORKSPACE_NAME", "team"),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            openrouter_default_model=os.getenv(
                "OPENROUTER_DEFAULT_MODEL",
                os.getenv("OPENROUTER_MODEL", "openai/gpt-4.1-mini"),
            ),
            openrouter_model=os.getenv(
                "OPENROUTER_DEFAULT_MODEL",
                os.getenv("OPENROUTER_MODEL", "openai/gpt-4.1-mini"),
            ),
            openrouter_base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            openrouter_site_url=os.getenv("OPENROUTER_SITE_URL", ""),
            openrouter_app_name=os.getenv("OPENROUTER_APP_NAME", "AI Kurator V2"),
            openrouter_free_text_models=os.getenv("OPENROUTER_FREE_TEXT_MODELS", ""),
            openrouter_free_vision_models=os.getenv("OPENROUTER_FREE_VISION_MODELS", ""),
            openrouter_cheap_text_models=os.getenv("OPENROUTER_CHEAP_TEXT_MODELS", ""),
            openrouter_cheap_vision_models=os.getenv("OPENROUTER_CHEAP_VISION_MODELS", ""),
            openrouter_quality_text_models=os.getenv("OPENROUTER_QUALITY_TEXT_MODELS", ""),
            openrouter_quality_vision_models=os.getenv("OPENROUTER_QUALITY_VISION_MODELS", ""),
            openrouter_vision_model=os.getenv(
                "OPENROUTER_VISION_MODEL",
                os.getenv("VISION_MODEL", "openai/gpt-4.1-mini"),
            ),
            vision_model=os.getenv("OPENROUTER_VISION_MODEL", os.getenv("VISION_MODEL", "openai/gpt-4.1-mini")),
            allow_quality_to_cheap_fallback=os.getenv("ALLOW_QUALITY_TO_CHEAP_FALLBACK", "false").lower()
            == "true",
            embedding_provider=os.getenv("EMBEDDING_PROVIDER", "local"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"),
            ollama_embedding_model=os.getenv("OLLAMA_EMBEDDING_MODEL", "BAAI/bge-m3"),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            embedding_dim=int(os.getenv("EMBEDDING_DIM", "1024") or "1024"),
            rag_pipeline_version=os.getenv("RAG_PIPELINE_VERSION", os.getenv("SCHEMA_VERSION", "v2")),
            owner_ids=os.getenv("OWNER_IDS", ""),
        )


if __name__ == "__main__":
    main()
