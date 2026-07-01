"""Show service/docs registry status without crawling new pages."""

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
from app.service_registry.config import DEFAULT_SERVICE_REGISTRY_CONFIG, load_service_registry_config  # noqa: E402
from app.service_registry.provider import ServiceDocsStatusProvider  # noqa: E402
from app.service_registry.status import status_payload  # noqa: E402
from app.service_registry.types import ServiceDefinition, ServiceDocsStatus  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse command-line args."""
    parser = argparse.ArgumentParser(description="Inspect service/docs registry status.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--scan-corpus", action="store_true", help="Count service mentions in indexed corpus rows.")
    parser.add_argument("--service", help="Filter by service_id or alias.")
    parser.add_argument("--registry-config", type=Path, default=DEFAULT_SERVICE_REGISTRY_CONFIG)
    parser.add_argument("--external-config", type=Path, default=DEFAULT_EXTERNAL_DOCS_CONFIG)
    parser.add_argument("--limit", type=int, default=10000, help="Max rows per table to inspect.")
    return parser.parse_args()


async def main_async() -> int:
    """Run status check."""
    args = parse_args()
    registry = load_service_registry_config(args.registry_config)
    services = _filter_services(registry.services, args.service)
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        payload = {
            "status": "disabled",
            "reason": "missing Supabase settings",
            "services": [service.to_dict() for service in services],
        }
        _print_payload(payload, json_mode=args.json)
        return 2

    async with SupabaseClient(settings) as client:
        provider = ServiceDocsStatusProvider(
            client,
            registry_config_path=args.registry_config,
            external_config_path=args.external_config,
            limit=args.limit,
        )
        statuses = await provider.list_statuses(scan_corpus=args.scan_corpus, service=args.service)
    payload = status_payload(statuses)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_human(statuses, scan_corpus=args.scan_corpus)
    return 0


def _filter_services(services: tuple[ServiceDefinition, ...], query: str | None) -> tuple[ServiceDefinition, ...]:
    if not query:
        return services
    needle = query.strip().casefold()
    result = tuple(
        service
        for service in services
        if service.service_id.casefold() == needle
        or service.display_name.casefold() == needle
        or any(alias.casefold() == needle for alias in service.aliases)
    )
    if not result:
        raise SystemExit(f"Unknown service: {query}")
    return result


def _print_payload(payload: dict[str, object], *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Service docs registry: {payload.get('status')}")
        if payload.get("reason"):
            print(f"Reason: {payload['reason']}")


def _print_human(statuses: tuple[ServiceDocsStatus, ...], *, scan_corpus: bool) -> None:
    for status in statuses:
        print(f"{status.display_name}:")
        print(f"  service_id: {status.service_id}")
        print(f"  aliases: {', '.join(status.aliases)}")
        print(f"  docs_source: {status.docs_source or 'none'}")
        print(f"  configured_status: {status.configured_status}")
        print(f"  docs_status: {status.docs_status}")
        print(f"  active_docs: {status.active_docs_count}")
        print(f"  active_chunks: {status.active_chunks_count}")
        print(f"  quality: {status.quality_status}")
        if scan_corpus:
            print(f"  detected_documents: {status.detected_documents_count}")
            print(f"  detected_chunks: {status.detected_chunks_count}")
            print(f"  mention_count: {status.mention_count or 0}")
        if status.notes:
            print(f"  notes: {'; '.join(status.notes)}")
        print("")


def main() -> None:
    """CLI entry point."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
