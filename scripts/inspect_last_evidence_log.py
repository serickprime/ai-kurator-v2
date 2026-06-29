"""Inspect the latest evidence log without exposing secrets or full chunks."""

from __future__ import annotations

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


async def main_async() -> int:
    """Print compact diagnostics for the newest evidence log."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        print("Last evidence log: disabled")
        print("Missing Supabase settings.")
        return 2

    async with SupabaseClient(settings) as client:
        rows = await client.select(
            "evidence_logs",
            params={
                "select": "question,document_candidates,evidence_pack,final_answer,final_sources,created_at",
                "order": "created_at.desc",
                "limit": "1",
            },
        )
    if not rows:
        print("Last evidence log: empty")
        return 0

    row = rows[0]
    evidence_pack = _dict(row.get("evidence_pack"))
    generation = _dict(evidence_pack.get("generation"))
    items = [_dict(item) for item in evidence_pack.get("items") or []]
    decisions = [_dict(item) for item in evidence_pack.get("decisions") or []]
    labels = [_dict(item) for item in evidence_pack.get("source_label_debug") or []]
    warnings = _warnings(row, evidence_pack)
    payload = {
        "created_at": row.get("created_at"),
        "question": row.get("question"),
        "answer_mode": evidence_pack.get("answer_mode"),
        "document_candidates": [
            {
                "filename": candidate.get("filename"),
                "title": candidate.get("title"),
                "score": candidate.get("score"),
                "route": candidate.get("route"),
            }
            for candidate in [_dict(item) for item in row.get("document_candidates") or []]
        ],
        "evidence_items": [
            {
                "evidence_id": item.get("evidence_id"),
                "document_title": item.get("document_title"),
                "locator": item.get("locator"),
                "score": item.get("score"),
                "preview": _preview(item.get("text")),
            }
            for item in items
        ],
        "evidence_decisions": [
            {
                "evidence_id": decision.get("evidence_id"),
                "status": decision.get("status"),
                "reasons": decision.get("reasons"),
                "score": decision.get("score"),
            }
            for decision in decisions
        ],
        "source_labels": [label.get("label") for label in labels],
        "final_sources": row.get("final_sources") or [],
        "llm_model_attempts": generation.get("llm_model_attempts") or [],
        "llm_errors_sanitized": generation.get("llm_errors_sanitized") or [],
        "final_model_used": generation.get("final_model_used"),
        "fallback_used": bool(generation.get("fallback_used", False)),
        "warnings": warnings,
        "answer_preview": _preview(row.get("final_answer"), limit=500),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _warnings(row: dict[str, Any], evidence_pack: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    final_answer = str(row.get("final_answer") or "")
    final_sources = [str(item) for item in row.get("final_sources") or []]
    if _looks_like_raw_dump(final_answer):
        warnings.append("answer_looks_like_raw_evidence_dump")
    if any(_bad_source_label(label) for label in final_sources):
        warnings.append("source_labels_need_cleanup")
    if len(evidence_pack.get("items") or []) > 5:
        warnings.append("too_many_evidence_items")
    generation = _dict(evidence_pack.get("generation"))
    if generation.get("fallback_used"):
        warnings.append("deterministic_fallback_used")
    return warnings


def _looks_like_raw_dump(answer: str) -> bool:
    lowered = answer.casefold()
    markers = ("====================", "текст страницы", "страница ", "визуальные элементы")
    return any(marker in lowered for marker in markers)


def _bad_source_label(label: str) -> bool:
    lowered = label.casefold()
    return "название файла" in lowered or "прочее" in lowered or "unknown" in lowered


def _preview(value: object, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def main() -> None:
    """CLI entry point."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
