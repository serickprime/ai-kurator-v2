"""Smoke-check Supabase connectivity for AI Kurator V2."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.db.supabase_client import SupabaseClient  # noqa: E402


async def main_async() -> int:
    """Run a read-only Supabase smoke check."""
    settings = get_settings()
    missing = [
        name
        for name, value in (
            ("SUPABASE_URL", settings.supabase_url),
            ("SUPABASE_SERVICE_ROLE_KEY", settings.supabase_service_role_key),
            ("DEFAULT_WORKSPACE_ID", settings.default_workspace_id),
        )
        if not value or str(value).startswith("replace_with")
    ]
    if missing:
        print(f"Missing required Supabase settings: {', '.join(missing)}")
        return 2

    try:
        async with SupabaseClient(settings) as supabase:
            rows = await supabase.select(
                "workspaces",
                params={
                    "select": "id,name",
                    "id": f"eq.{settings.default_workspace_id}",
                    "limit": "1",
                },
            )
            if not rows:
                print("Supabase connected, but DEFAULT_WORKSPACE_ID was not found in workspaces.")
                return 1
            print(f"Supabase OK: workspace={rows[0].get('name') or rows[0].get('id')}")
            return 0
    except Exception as exc:  # noqa: BLE001 - smoke script should print actionable failure
        print(f"Supabase smoke failed: {exc}")
        return 1


def main() -> None:
    """CLI entry point."""
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
