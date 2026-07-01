"""Show external documentation indexing status without printing secrets."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.db.supabase_client import SupabaseClient  # noqa: E402
from app.external_docs.config import DEFAULT_EXTERNAL_DOCS_CONFIG, load_external_docs_config  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(description="Inspect external docs index status.")
    parser.add_argument("--config", type=Path, default=DEFAULT_EXTERNAL_DOCS_CONFIG, help="External docs YAML path.")
    parser.add_argument("--limit", type=int, default=10000, help="Max external docs rows to inspect.")
    return parser.parse_args()


async def main_async() -> int:
    """Run status check."""
    args = parse_args()
    config = load_external_docs_config(args.config)
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        print(json.dumps({"status": "disabled", "reason": "missing Supabase settings"}, indent=2))
        return 2

    async with SupabaseClient(settings) as client:
        rows = await client.select(
            "documents",
            params={
                "select": "id,filename,document_key,status,content_hash,metadata,updated_at",
                "source_type": "eq.external_docs",
                "limit": str(args.limit),
            },
        )

    payload = {"status": "ok", "sources": [_source_status(source, rows) for source in config.sources]}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _source_status(source: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
    matching = [row for row in rows if _metadata(row).get("source_name") == source.name]
    active = [row for row in matching if row.get("status") == "active"]
    archived = [row for row in matching if row.get("status") == "archived"]
    latest = max(matching, key=lambda row: str(_metadata(row).get("crawled_at") or row.get("updated_at") or ""), default={})
    latest_metadata = _metadata(latest)
    return {
        "source_name": source.name,
        "domains": list(source.allowed_domains),
        "configured_start_urls": list(source.start_urls),
        "documents_total": len(matching),
        "active": len(active),
        "archived": len(archived),
        "latest_crawled_at": latest_metadata.get("crawled_at"),
        "latest_content_hash": latest_metadata.get("content_hash") or latest.get("content_hash"),
        "warning": "empty source index" if not matching else "",
    }


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def main() -> None:
    """CLI entry point."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
