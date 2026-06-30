"""Small quality smoke checks for the latest RAG answer."""

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
from app.ingestion.text_normalizer import is_boilerplate_label  # noqa: E402


async def main_async() -> int:
    """Run lightweight checks against the latest evidence log."""
    settings = get_settings()
    async with SupabaseClient(settings) as client:
        rows = await client.select(
            "evidence_logs",
            params={
                "select": "question,evidence_pack,final_answer,final_sources,created_at",
                "order": "created_at.desc",
                "limit": "1",
            },
        )
    if not rows:
        print("Answer quality smoke: no evidence logs")
        return 0

    row = rows[0]
    checks = _checks(row)
    passed = all(checks.values())
    print(
        json.dumps(
            {
                "created_at": row.get("created_at"),
                "question": row.get("question"),
                "passed": passed,
                "checks": checks,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


def _checks(row: dict[str, Any]) -> dict[str, bool]:
    answer = str(row.get("final_answer") or "")
    sources = [str(item) for item in row.get("final_sources") or []]
    pack = row.get("evidence_pack") if isinstance(row.get("evidence_pack"), dict) else {}
    mode = str(pack.get("answer_mode") or "")
    items = pack.get("items") or []
    return {
        "no_raw_evidence_dump": not _looks_like_raw_dump(answer),
        "no_internal_pipeline_terms": "evidence pack" not in answer.casefold(),
        "source_labels_clean": not any(_bad_source_label(source) for source in sources),
        "evidence_count_reasonable": len(items) <= 5,
        "fallback_not_empty": bool(answer.strip()),
        "no_sources_when_no_evidence": bool(sources) is False if mode in {"out_of_base", "ask_for_missing_data"} else True,
    }


def _looks_like_raw_dump(answer: str) -> bool:
    lowered = answer.casefold()
    markers = ("====================", "текст страницы", "страница ", "визуальные элементы")
    return any(marker in lowered for marker in markers)


def _bad_source_label(label: str) -> bool:
    lowered = label.casefold()
    return is_boilerplate_label(label) or "unknown" in lowered


def main() -> None:
    """CLI entry point."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
