"""Show service/docs registry status without crawling new pages."""

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
from app.service_registry.config import DEFAULT_SERVICE_REGISTRY_CONFIG, load_service_registry_config  # noqa: E402
from app.service_registry.status import (  # noqa: E402
    build_service_docs_statuses,
    count_service_mentions,
    status_payload,
)
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
    external_config = load_external_docs_config(args.external_config)
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
        external_documents = await _load_external_documents(client, limit=args.limit)
        active_external_ids = [
            str(row.get("id") or "")
            for row in external_documents
            if row.get("status") == "active"
        ]
        external_chunks = await _load_chunks(client, active_external_ids, limit=args.limit)
        mention_counts = (
            await _load_mention_counts(client, services=registry.services, limit=args.limit)
            if args.scan_corpus
            else None
        )

    statuses = build_service_docs_statuses(
        services=services,
        configured_docs_sources=(source.name for source in external_config.sources),
        documents=external_documents,
        chunks=external_chunks,
        mention_counts=mention_counts,
    )
    payload = status_payload(statuses)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_human(statuses, scan_corpus=args.scan_corpus)
    return 0


async def _load_external_documents(client: SupabaseClient, *, limit: int) -> list[dict[str, Any]]:
    return await client.select(
        "documents",
        params={
            "select": "id,filename,document_key,title,status,metadata,updated_at",
            "source_type": "eq.external_docs",
            "limit": str(limit),
        },
    )


async def _load_chunks(client: SupabaseClient, document_ids: list[str], *, limit: int) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for group in _batches([document_id for document_id in document_ids if document_id], 20):
        chunks.extend(
            await client.select(
                "chunks",
                params={
                    "select": "id,document_id,chunk_index,content,heading,metadata",
                    "document_id": f"in.({','.join(group)})",
                    "limit": str(limit),
                },
            )
        )
    return chunks


async def _load_mention_counts(
    client: SupabaseClient,
    *,
    services: tuple[ServiceDefinition, ...],
    limit: int,
) -> dict[str, int]:
    documents = await client.select(
        "documents",
        params={
            "select": "id,filename,title,course,module,lesson,status,source_type,metadata",
            "status": "eq.active",
            "limit": str(limit),
        },
    )
    active_ids = [str(row.get("id") or "") for row in documents]
    chunks = await _load_chunks(client, active_ids, limit=limit)
    cards = await _load_document_cards(client, active_ids, limit=limit)
    return count_service_mentions(
        services=services,
        corpus_rows=[*documents, *cards, *chunks],
    )


async def _load_document_cards(client: SupabaseClient, document_ids: list[str], *, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in _batches([document_id for document_id in document_ids if document_id], 20):
        rows.extend(
            await client.select(
                "document_cards",
                params={
                    "select": (
                        "document_id,summary,topics,questions_answered,entities,task_types,not_about,metadata"
                    ),
                    "document_id": f"in.({','.join(group)})",
                    "limit": str(limit),
                },
            )
        )
    return rows


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
            print(f"  mention_count: {status.mention_count or 0}")
        if status.notes:
            print(f"  notes: {'; '.join(status.notes)}")
        print("")


def _batches(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def main() -> None:
    """CLI entry point."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
