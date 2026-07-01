"""Rebuild corpus-aware term statistics in Supabase."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Rebuild term_statistics for one workspace.")
    parser.add_argument("--workspace", default="team", help="Workspace name to resolve when --workspace-id is absent.")
    parser.add_argument("--workspace-id", help="Workspace UUID.")
    return parser.parse_args()


async def main_async() -> int:
    """Run term statistics rebuild."""
    from app.db.supabase_client import SupabaseRequestError
    from app.db.supabase_client import SupabaseClient

    args = parse_args()
    settings = _load_settings()
    async with SupabaseClient(settings) as supabase:
        workspace_id = args.workspace_id or await _workspace_id(supabase, args.workspace)
        if not workspace_id:
            raise SystemExit(f"Workspace not found: {args.workspace}")

        try:
            rows = await supabase.rpc("refresh_term_statistics", {"p_workspace_id": workspace_id})
        except SupabaseRequestError as exc:
            if exc.is_missing_relation:
                print("term_statistics: missing")
                print("Apply app/db/schema.sql to create public.term_statistics and refresh_term_statistics().")
                return 2
            raise
        refreshed = _rpc_count(rows)
        try:
            sample = await supabase.select(
                "term_statistics",
                params={
                    "select": "term,document_frequency,chunk_frequency,term_type_guess",
                    "workspace_id": f"eq.{workspace_id}",
                    "order": "document_frequency.desc",
                    "limit": "10",
                },
            )
        except SupabaseRequestError as exc:
            if exc.is_missing_relation:
                sample = []
            else:
                raise

    print(f"workspace_id={workspace_id}")
    print(f"refreshed_terms={refreshed}")
    print("top_terms:")
    for row in sample:
        print(
            "- "
            f"{row.get('term')} "
            f"df={row.get('document_frequency')} "
            f"chunks={row.get('chunk_frequency')} "
            f"type={row.get('term_type_guess')}"
        )
    return 0


async def _workspace_id(supabase: Any, workspace_name: str) -> str | None:
    rows = await supabase.select(
        "workspaces",
        params={"select": "id", "name": f"eq.{workspace_name}", "limit": "1"},
    )
    if not rows:
        return None
    return str(rows[0]["id"])


def _rpc_count(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    value = next(iter(rows[0].values()), 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _load_settings() -> Any:
    try:
        from app.config import get_settings

        return get_settings()
    except ModuleNotFoundError as exc:
        if exc.name != "pydantic_settings":
            raise
        import os

        return SimpleNamespace(
            supabase_url=os.getenv("SUPABASE_URL", ""),
            supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
        )


def main() -> None:
    """CLI entry point."""
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
