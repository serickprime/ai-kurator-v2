"""Read-only docs source health and staleness report."""

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
from app.external_docs.config import DEFAULT_EXTERNAL_DOCS_CONFIG  # noqa: E402
from app.service_registry.config import DEFAULT_SERVICE_REGISTRY_CONFIG  # noqa: E402
from app.service_registry.docs_health import (  # noqa: E402
    DEFAULT_DOCS_HEALTH_POLICY_CONFIG,
    build_docs_health_report,
    build_local_config_statuses,
    external_refresh_days_by_source,
    filter_docs_health_report,
    format_docs_health_report,
    load_docs_health_policy,
    load_external_documents_for_health,
)
from app.service_registry.provider import ServiceDocsStatusProvider  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Report docs source health without writes or refreshes.")
    parser.add_argument("--service", help="Filter by service_id, display name, or docs source.")
    parser.add_argument("--status", help="Filter by health, docs, or quality status.")
    parser.add_argument("--stale-only", action="store_true", help="Show only stale sources.")
    parser.add_argument("--limit", type=int, default=10000, help="Max rows per table for read-only checks.")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Output format.")
    parser.add_argument("--registry-config", type=Path, default=DEFAULT_SERVICE_REGISTRY_CONFIG)
    parser.add_argument("--external-config", type=Path, default=DEFAULT_EXTERNAL_DOCS_CONFIG)
    parser.add_argument("--policy-config", type=Path, default=DEFAULT_DOCS_HEALTH_POLICY_CONFIG)
    return parser.parse_args()


async def main_async() -> int:
    """Run read-only docs health report."""
    args = parse_args()
    policy = load_docs_health_policy(args.policy_config)
    refresh_days = external_refresh_days_by_source(args.external_config)
    statuses, documents, runtime_status = await _load_runtime_inputs(args)
    report = build_docs_health_report(
        statuses=statuses,
        documents=documents,
        policy=policy,
        external_refresh_days=refresh_days,
        runtime_status=runtime_status,
    )
    report = filter_docs_health_report(
        report,
        service=args.service,
        status=args.status,
        stale_only=args.stale_only,
        limit=args.limit,
    )
    if args.format == "json":
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_docs_health_report(report))
    return 0


async def _load_runtime_inputs(args: argparse.Namespace) -> tuple[tuple[object, ...], list[dict[str, object]], str]:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return (
            build_local_config_statuses(
                registry_config_path=args.registry_config,
                external_config_path=args.external_config,
            ),
            [],
            "unavailable: missing Supabase settings",
        )

    try:
        async with SupabaseClient(settings) as client:
            provider = ServiceDocsStatusProvider(
                client,
                registry_config_path=args.registry_config,
                external_config_path=args.external_config,
                limit=args.limit,
            )
            statuses = await provider.list_statuses(scan_corpus=False)
            documents = await load_external_documents_for_health(client, limit=args.limit)
        return statuses, documents, "available"
    except Exception as exc:  # noqa: BLE001 - CLI must degrade without traceback
        return (
            build_local_config_statuses(
                registry_config_path=args.registry_config,
                external_config_path=args.external_config,
            ),
            [],
            f"unavailable: {exc.__class__.__name__}",
        )


def main() -> None:
    """CLI entry point."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
