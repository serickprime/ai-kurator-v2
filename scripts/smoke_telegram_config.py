"""Smoke-check Telegram and required runtime configuration."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import UUID

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.bot.access import parse_telegram_ids  # noqa: E402
from app.config import get_settings  # noqa: E402


def _missing_required_settings() -> list[str]:
    settings = get_settings()
    pairs = (
        ("TELEGRAM_BOT_TOKEN", settings.telegram_bot_token),
        ("OWNER_IDS", settings.owner_ids),
        ("SUPABASE_URL", settings.supabase_url),
        ("SUPABASE_SERVICE_ROLE_KEY", settings.supabase_service_role_key),
        ("DEFAULT_WORKSPACE_ID", settings.default_workspace_id),
        ("OPENROUTER_API_KEY", settings.openrouter_api_key),
        ("OPENROUTER_DEFAULT_MODEL", settings.openrouter_default_model),
        ("OPENROUTER_VISION_MODEL", settings.openrouter_vision_model),
        ("EMBEDDING_PROVIDER", settings.embedding_provider),
        ("EMBEDDING_MODEL", settings.embedding_model),
        ("EMBEDDING_DIM", settings.embedding_dim),
        ("RAG_PIPELINE_VERSION", settings.rag_pipeline_version),
    )
    return [
        name
        for name, value in pairs
        if value is None or str(value).strip() == "" or str(value).startswith("replace_with")
    ]


async def main_async() -> int:
    """Validate first-run settings and Telegram token."""
    settings = get_settings()
    missing = _missing_required_settings()
    if missing:
        print(f"Missing required settings: {', '.join(missing)}")
        return 2

    owner_ids = parse_telegram_ids(settings.owner_ids)
    if not owner_ids:
        print("OWNER_IDS must contain at least one numeric Telegram user id.")
        return 2
    if settings.embedding_dim != 1024:
        print(f"EMBEDDING_DIM must be 1024 for schema vector(1024), got {settings.embedding_dim}.")
        return 2
    if settings.rag_pipeline_version != "v2":
        print(f"RAG_PIPELINE_VERSION must be v2, got {settings.rag_pipeline_version}.")
        return 2
    try:
        UUID(settings.default_workspace_id)
    except ValueError:
        print("DEFAULT_WORKSPACE_ID must be a workspace UUID.")
        return 2

    try:
        async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
            response = await client.get(f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe")
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                print("Telegram getMe returned ok=false.")
                return 1
            bot = data.get("result") or {}
            print(f"Telegram OK: @{bot.get('username') or bot.get('first_name') or 'bot'}")
            return 0
    except httpx.HTTPStatusError as exc:
        print(f"Telegram smoke failed: HTTP {exc.response.status_code}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Telegram smoke failed: {exc.__class__.__name__}")
        return 1


def main() -> None:
    """CLI entry point."""
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
