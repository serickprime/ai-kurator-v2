"""Validate indexed external docs quality without crawling new pages."""

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
from app.external_docs.validation import ExternalDocsValidationResult, validate_external_docs  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Validate indexed external docs quality.")
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--source", help="Source name from config/external_docs.yaml.")
    source_group.add_argument("--all", action="store_true", help="Validate all configured sources.")
    parser.add_argument("--sample", type=int, default=10, help="Maximum examples to show per check.")
    parser.add_argument("--fail-on-warnings", action="store_true", help="Return non-zero for WARN as well as FAIL.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    parser.add_argument("--config", type=Path, default=DEFAULT_EXTERNAL_DOCS_CONFIG, help="External docs YAML path.")
    return parser.parse_args()


async def main_async() -> int:
    """Run validation."""
    args = parse_args()
    config = load_external_docs_config(args.config)
    sources = config.sources if args.all else (config.source(args.source),)
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        payload = {"status": "disabled", "reason": "missing Supabase settings"}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    async with SupabaseClient(settings) as client:
        documents = await client.select(
            "documents",
            params={
                "select": "id,filename,document_key,title,status,metadata,updated_at",
                "source_type": "eq.external_docs",
                "limit": "10000",
            },
        )
        active_ids = [
            str(row.get("id") or "")
            for row in documents
            if row.get("status") == "active"
            and _metadata(row).get("source_name") in {source.name for source in sources}
        ]
        chunks = await _load_chunks(client, active_ids)

    results = [
        validate_external_docs(
            source_name=source.name,
            documents=documents,
            chunks=chunks,
            sample_size=args.sample,
        )
        for source in sources
    ]
    payload = {
        "status": _overall_quality(results),
        "sources": [result.to_dict() for result in results],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_human(results)

    if any(result.quality == "FAIL" for result in results):
        return 1
    if args.fail_on_warnings and any(result.quality == "WARN" for result in results):
        return 1
    return 0


async def _load_chunks(client: SupabaseClient, document_ids: list[str]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for group in _batches([document_id for document_id in document_ids if document_id], 20):
        chunks.extend(
            await client.select(
                "chunks",
                params={
                    "select": "id,document_id,chunk_index,content,heading,metadata",
                    "document_id": f"in.({','.join(group)})",
                    "limit": "10000",
                },
            )
        )
    return chunks


def _print_human(results: list[ExternalDocsValidationResult]) -> None:
    for result in results:
        print(f"QUALITY: {result.quality}")
        print(f"Source: {result.source_name}")
        print("Metrics:")
        for key, value in result.metrics.items():
            print(f"- {key}: {value}")
        if result.failures:
            print("Failures:")
            for item in result.failures:
                print(f"- {item}")
        if result.warnings:
            print("Warnings:")
            for item in result.warnings:
                print(f"- {item}")
        sample_rows = [
            (key, values)
            for key, values in result.samples.items()
            if values
        ]
        if sample_rows:
            print("Samples:")
            for key, values in sample_rows:
                print(f"- {key}:")
                for value in values:
                    print(f"  - {value}")
        print("")


def _overall_quality(results: list[ExternalDocsValidationResult]) -> str:
    if any(result.quality == "FAIL" for result in results):
        return "FAIL"
    if any(result.quality == "WARN" for result in results):
        return "WARN"
    return "PASS"


def _batches(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


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
