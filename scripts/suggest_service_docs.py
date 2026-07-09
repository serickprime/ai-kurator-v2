"""Read-only service-aware docs suggestion preview."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.db.supabase_client import SupabaseClient  # noqa: E402
from app.docs_registry.candidates import DEFAULT_DOCS_SOURCE_CANDIDATES_CONFIG  # noqa: E402
from app.external_docs.config import DEFAULT_EXTERNAL_DOCS_CONFIG  # noqa: E402
from app.service_registry.config import DEFAULT_SERVICE_REGISTRY_CONFIG  # noqa: E402
from app.service_registry.provider import ServiceDocsStatusProvider  # noqa: E402
from app.service_registry.suggestions import (  # noqa: E402
    DEFAULT_SERVICE_SUGGESTION_ALIASES_CONFIG,
    ServiceSuggestionEngine,
    format_service_suggestion_report,
    load_service_suggestion_catalog,
)
from app.service_registry.types import ServiceDocsStatus  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Preview service-aware docs suggestions without writes.")
    parser.add_argument("--question", required=True, help="User question to inspect.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--limit", type=int, default=10000, help="Max rows per table for read-only status checks.")
    parser.add_argument("--registry-config", type=Path, default=DEFAULT_SERVICE_REGISTRY_CONFIG)
    parser.add_argument("--external-config", type=Path, default=DEFAULT_EXTERNAL_DOCS_CONFIG)
    parser.add_argument("--docs-candidates-config", type=Path, default=DEFAULT_DOCS_SOURCE_CANDIDATES_CONFIG)
    parser.add_argument("--suggestion-aliases-config", type=Path, default=DEFAULT_SERVICE_SUGGESTION_ALIASES_CONFIG)
    return parser.parse_args()


async def main_async() -> int:
    """Run a read-only service-aware suggestion preview."""
    args = parse_args()
    catalog = load_service_suggestion_catalog(
        registry_config_path=args.registry_config,
        docs_candidates_config_path=args.docs_candidates_config,
        suggestion_aliases_config_path=args.suggestion_aliases_config,
    )
    statuses, runtime_status = await _load_statuses(args)
    suggestion = ServiceSuggestionEngine(catalog, statuses=statuses).suggest(args.question)

    if args.json:
        print(
            json.dumps(
                {
                    "mode": "read-only",
                    "runtime_status": runtime_status,
                    "suggestion": suggestion.to_dict(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(format_service_suggestion_report(suggestion, runtime_status=runtime_status))
    return 0


async def _load_statuses(args: argparse.Namespace) -> tuple[tuple[ServiceDocsStatus, ...], str]:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return (), "unavailable: missing Supabase settings"

    try:
        async with SupabaseClient(settings) as client:
            provider = ServiceDocsStatusProvider(
                client,
                registry_config_path=args.registry_config,
                external_config_path=args.external_config,
                limit=args.limit,
            )
            statuses = await provider.list_statuses(scan_corpus=False)
        return statuses, "available"
    except Exception as exc:  # noqa: BLE001 - CLI must degrade without traceback
        return (), f"unavailable: {exc.__class__.__name__}"


def main() -> None:
    """CLI entry point."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
