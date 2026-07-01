"""Inspect stored ingestion quality for one document without printing secrets."""

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
from app.ingestion.text_normalizer import has_suspicious_glued_cyrillic_text, is_boilerplate_label  # noqa: E402
from app.rag.source_labels import SourceLabelBuilder  # noqa: E402
from app.rag.types import SourceRef  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description="Inspect one ingested document by filename.")
    parser.add_argument("--filename", required=True, help="Filename or document_key to inspect.")
    parser.add_argument("--workspace-id", default="", help="Optional workspace UUID filter.")
    parser.add_argument("--chunk-limit", type=int, default=12, help="Number of chunk previews to inspect.")
    return parser.parse_args()


async def main_async() -> int:
    """Run document inspection."""
    args = parse_args()
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        print("Inspect ingested document: disabled")
        print("Missing Supabase settings.")
        return 2

    async with SupabaseClient(settings) as client:
        document = await _load_document(client, args.filename, args.workspace_id)
        if not document:
            print(json.dumps({"status": "not_found", "filename": args.filename}, ensure_ascii=False, indent=2))
            return 1
        document_id = str(document.get("id") or "")
        sections = await client.select(
            "sections",
            params={
                "select": "id,section_index,heading,page_start,page_end,metadata",
                "document_id": f"eq.{document_id}",
                "order": "section_index.asc",
                "limit": "20",
            },
        )
        chunks = await client.select(
            "chunks",
            params={
                "select": "id,chunk_index,content,heading,page,metadata",
                "document_id": f"eq.{document_id}",
                "order": "chunk_index.asc",
                "limit": str(args.chunk_limit),
            },
        )
        chunk_rows = await client.select(
            "chunks",
            params={
                "select": "id",
                "document_id": f"eq.{document_id}",
                "limit": "10000",
            },
        )

    payload = _payload(document, sections, chunks, chunks_count=len(chunk_rows))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


async def _load_document(client: SupabaseClient, filename: str, workspace_id: str) -> dict[str, Any] | None:
    params = {
        "select": "id,workspace_id,filename,document_key,title,status,metadata,created_at,updated_at",
        "or": f"(filename.eq.{filename},document_key.eq.{filename})",
        "order": "updated_at.desc.nullslast,created_at.desc",
        "limit": "1",
    }
    if workspace_id:
        params["workspace_id"] = f"eq.{workspace_id}"
    rows = await client.select("documents", params=params)
    return rows[0] if rows else None


def _payload(
    document: dict[str, Any],
    sections: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    *,
    chunks_count: int,
) -> dict[str, Any]:
    label_builder = SourceLabelBuilder()
    source_refs = [
        SourceRef(
            document_id=str(document.get("id") or ""),
            document_title=str(document.get("title") or ""),
            locator=str(chunk.get("heading") or ""),
            metadata={
                **_dict(chunk.get("metadata")),
                "filename": document.get("filename"),
                "title": document.get("title"),
            },
            evidence_id=str(chunk.get("id") or ""),
        )
        for chunk in chunks
    ]
    labels = label_builder.build_many(source_refs)
    raw_labels = [label_builder.build(source) for source in source_refs]
    chunk_previews = [_preview(chunk.get("content")) for chunk in chunks]
    headings = [str(section.get("heading") or "") for section in sections]
    bad_signs = {
        "title_is_boilerplate": is_boilerplate_label(document.get("title")),
        "heading_is_boilerplate": any(is_boilerplate_label(heading) for heading in headings),
        "duplicated_source_labels": len(raw_labels) != len(set(label.casefold() for label in raw_labels)),
        "suspicious_glued_cyrillic_text": any(has_suspicious_glued_cyrillic_text(preview) for preview in chunk_previews),
        "source_file_leaked_into_chunk": any(_source_file_leaked(preview, str(document.get("filename") or "")) for preview in chunk_previews),
    }
    return {
        "status": "ok",
        "document": {
            "id": document.get("id"),
            "filename": document.get("filename"),
            "document_key": document.get("document_key"),
            "title": document.get("title"),
            "clean_label": label_builder.build_document_label(document),
            "source_kind": _dict(document.get("metadata")).get("source_kind"),
            "source_url": _dict(document.get("metadata")).get("source_url"),
            "canonical_url": _dict(document.get("metadata")).get("canonical_url"),
            "crawled_at": _dict(document.get("metadata")).get("crawled_at"),
            "content_hash": _dict(document.get("metadata")).get("content_hash"),
            "freshness_status": _dict(document.get("metadata")).get("freshness_status"),
        },
        "sections_count": len(sections),
        "chunks_count": chunks_count,
        "chunks_count_sampled": len(chunks),
        "section_headings": headings[:10],
        "chunk_previews": chunk_previews,
        "source_labels": labels,
        "bad_signs": bad_signs,
    }


def _preview(value: object, limit: int = 220) -> str:
    return " ".join(str(value or "").split())[:limit]


def _source_file_leaked(text: str, filename: str) -> bool:
    lowered = text.casefold()
    if "source file:" in lowered:
        return True
    return bool(filename and f"source file: {filename}".casefold() in lowered)


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def main() -> None:
    """CLI entry point."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
