"""Smoke-check Telegram upload ingestion without sending Telegram messages."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ingestion.runtime import build_ingestion_runtime_from_settings, validate_ingestion_config  # noqa: E402


async def main_async() -> int:
    """Run a real local upload-ingestion smoke test when config is available."""
    settings = _load_settings()
    validation = validate_ingestion_config(settings)
    if not validation.ready:
        print("Upload ingestion: disabled")
        print("Missing:")
        for item in validation.missing:
            print(f"- {item}")
        return 0

    runtime = build_ingestion_runtime_from_settings(settings)
    if runtime is None:
        print("Upload ingestion: disabled")
        print("Builder returned None. Check logs for details.")
        return 0

    try:
        with tempfile.TemporaryDirectory(prefix="ai-kurator-upload-smoke-") as tmpdir:
            path = Path(tmpdir) / f"telegram-upload-smoke-{uuid4().hex}.txt"
            path.write_text(
                "\n".join(
                    [
                        "# Telegram upload smoke material",
                        "",
                        "This controlled material verifies that Telegram uploads reach Supabase ingestion.",
                        "",
                        "## Local install note",
                        "",
                        "FACT-ID: TELEGRAM_UPLOAD_SMOKE",
                        "The document should create a document card, sections, chunks, and embeddings.",
                    ]
                ),
                encoding="utf-8",
            )

            results = await runtime.service.ingest_path(
                path,
                workspace=getattr(settings, "default_workspace_name", "team") or "team",
                course="smoke",
            )
            if not results:
                print("Upload ingestion: failed")
                print("No ingestion result returned.")
                return 1
            result = results[0]
            counts = await _verify_rows(runtime.resources.supabase, result.document_id)
            embedding_dim = await _chunk_embedding_dim(runtime.resources.supabase, result.document_id)

        expected_dim = int(getattr(settings, "embedding_dim", 0) or 0)
        if counts["chunks"] <= 0:
            print("Upload ingestion: failed")
            print("chunks=0")
            return 1
        if expected_dim and embedding_dim and embedding_dim != expected_dim:
            print("Upload ingestion: failed")
            print(f"Embedding dimension mismatch: expected={expected_dim} actual={embedding_dim}")
            return 1

        print("Upload ingestion: ready")
        print(f"document_id={result.document_id}")
        print(f"sections={counts['sections']}")
        print(f"chunks={counts['chunks']}")
        print(f"document_cards={counts['document_cards']}")
        print(f"embedding_dim={embedding_dim or 'unknown'}")
        print(f"term_statistics={getattr(result, 'term_statistics_status', 'skipped')}")
        return 0
    except Exception as exc:  # noqa: BLE001 - smoke should explain setup/runtime gaps
        print("Upload ingestion: failed")
        print(_safe_message(exc))
        return 1
    finally:
        await runtime.close()


async def _verify_rows(supabase: Any, document_id: str) -> dict[str, int]:
    card_rows = await supabase.select(
        "document_cards",
        params={"select": "id", "document_id": f"eq.{document_id}"},
    )
    section_rows = await supabase.select(
        "sections",
        params={"select": "id", "document_id": f"eq.{document_id}"},
    )
    chunk_rows = await supabase.select(
        "chunks",
        params={"select": "id", "document_id": f"eq.{document_id}"},
    )
    return {
        "document_cards": len(card_rows),
        "sections": len(section_rows),
        "chunks": len(chunk_rows),
    }


async def _chunk_embedding_dim(supabase: Any, document_id: str) -> int:
    rows = await supabase.select(
        "chunks",
        params={"select": "embedding", "document_id": f"eq.{document_id}", "limit": "1"},
    )
    if not rows:
        return 0
    return _embedding_dim(rows[0].get("embedding"))


def _embedding_dim(value: object) -> int:
    """Return dimension from a pgvector value returned by PostgREST."""
    if isinstance(value, list):
        return len(value)
    if isinstance(value, str):
        clean = value.strip()
        if clean.startswith("[") and clean.endswith("]"):
            body = clean[1:-1].strip()
            return 0 if not body else body.count(",") + 1
    return 0


def _safe_message(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if token:
        text = text.replace(token, "<redacted>")
    return text[:800]


def _load_settings() -> object:
    try:
        from app.config import get_settings

        return get_settings()
    except ModuleNotFoundError as exc:
        if exc.name != "pydantic_settings":
            raise
        return SimpleNamespace(
            supabase_url=os.getenv("SUPABASE_URL", ""),
            supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
            embedding_provider=os.getenv("EMBEDDING_PROVIDER", "local"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"),
            ollama_embedding_model=os.getenv("OLLAMA_EMBEDDING_MODEL", "BAAI/bge-m3"),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            embedding_dim=int(os.getenv("EMBEDDING_DIM", "1024") or "1024"),
            vision_enabled=False,
            default_workspace_name=os.getenv("DEFAULT_WORKSPACE_NAME", "team"),
        )


def main() -> None:
    """CLI entry point."""
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
