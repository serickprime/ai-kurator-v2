"""Smoke-check OpenRouter chat completion for AI Kurator V2."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402


async def main_async() -> int:
    """Run a minimal OpenRouter completion."""
    settings = get_settings()
    missing = [
        name
        for name, value in (
            ("OPENROUTER_API_KEY", settings.openrouter_api_key),
            ("OPENROUTER_DEFAULT_MODEL", settings.openrouter_default_model),
            ("OPENROUTER_VISION_MODEL", settings.openrouter_vision_model),
        )
        if not value or str(value).startswith("replace_with")
    ]
    if missing:
        print(f"Missing required OpenRouter settings: {', '.join(missing)}")
        return 2

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    if settings.openrouter_site_url:
        headers["HTTP-Referer"] = settings.openrouter_site_url
    if settings.openrouter_app_name:
        headers["X-Title"] = settings.openrouter_app_name

    try:
        async with httpx.AsyncClient(
            base_url=settings.openrouter_base_url.rstrip("/"),
            timeout=45.0,
            trust_env=False,
        ) as client:
            response = await client.post(
                "/chat/completions",
                headers=headers,
                json={
                    "model": settings.openrouter_default_model,
                    "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                    "temperature": 0,
                    "max_tokens": 8,
                },
            )
            response.raise_for_status()
            content = str(response.json()["choices"][0]["message"]["content"]).strip()
            if not content:
                print("OpenRouter responded, but completion content was empty.")
                return 1
            print(f"OpenRouter OK: model={settings.openrouter_default_model}, response={content[:40]}")
            return 0
    except httpx.HTTPStatusError as exc:
        print(f"OpenRouter smoke failed: HTTP {exc.response.status_code} {exc.response.text[:300]}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"OpenRouter smoke failed: {exc}")
        return 1


def main() -> None:
    """CLI entry point."""
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
