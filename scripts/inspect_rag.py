"""Inspect question analysis and document routing."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.rag.question_analysis import QuestionAnalyzer  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Inspect RAG v2 question analysis and document routing.")
    parser.add_argument("positional_question", nargs="?", help="Question to inspect.")
    parser.add_argument("--question", help="Question to inspect.")
    parser.add_argument("--workspace", default="team", help="Workspace name to resolve when --workspace-id is absent.")
    parser.add_argument("--workspace-id", help="Workspace UUID.")
    parser.add_argument("--course", default=None, help="Optional course filter.")
    parser.add_argument("--limit", type=int, default=5, help="Maximum document candidates.")
    return parser.parse_args()


async def main_async() -> None:
    """Run inspection."""
    args = parse_args()
    question = args.question or args.positional_question
    if not question:
        raise SystemExit("Provide a question with --question or as a positional argument.")

    analysis = QuestionAnalyzer().analyze(question)
    print("QuestionAnalysis")
    print(_json(asdict(analysis)))

    try:
        term_analysis = await _term_analysis(args, analysis)
        print("QueryTermAnalysis")
        print(_json(term_analysis))
    except Exception as exc:  # noqa: BLE001 - debug command should explain setup gaps
        print("QueryTermAnalysis")
        print(_json({"error": str(exc)}))

    try:
        candidates = await _route(args, analysis)
    except Exception as exc:  # noqa: BLE001 - debug command should explain setup gaps
        print("DocumentCandidates")
        print(_json({"error": str(exc)}))
        return

    print("DocumentCandidates")
    print(_json([asdict(candidate) for candidate in candidates]))


async def _route(args: argparse.Namespace, analysis: Any) -> tuple[Any, ...]:
    from app.db.supabase_client import SupabaseClient
    from app.llm.embeddings import OllamaEmbeddingClient
    from app.rag.document_router import DocumentRouter, SupabaseDocumentCardStore

    settings = _load_settings()
    embedding_client = OllamaEmbeddingClient(settings)
    async with SupabaseClient(settings) as supabase:
        try:
            workspace_id = args.workspace_id or await _workspace_id(supabase, args.workspace)
            if not workspace_id:
                raise RuntimeError(f"Workspace not found: {args.workspace}")

            router = DocumentRouter(
                store=SupabaseDocumentCardStore(supabase),
                embedding_client=embedding_client,
            )
            return await router.route(
                analysis,
                workspace_id=workspace_id,
                course=args.course,
                limit=args.limit,
            )
        finally:
            await embedding_client.close()


async def _term_analysis(args: argparse.Namespace, analysis: Any) -> dict[str, Any]:
    from app.db.supabase_client import SupabaseClient
    from app.rag.document_router import SupabaseDocumentCardStore
    from app.rag.term_scoring import CorpusTermScorer

    settings = _load_settings()
    async with SupabaseClient(settings) as supabase:
        workspace_id = args.workspace_id or await _workspace_id(supabase, args.workspace)
        if not workspace_id:
            raise RuntimeError(f"Workspace not found: {args.workspace}")
        store = SupabaseDocumentCardStore(supabase)
        rows = await store.list_term_statistics(workspace_id=workspace_id, course=args.course)
        query_terms = CorpusTermScorer.from_rows(rows).query_terms(analysis)
        return {
            "common_terms": list(query_terms.common_terms),
            "platform_terms": list(query_terms.platform_terms),
            "action_terms": list(query_terms.action_terms),
            "object_terms": list(query_terms.object_terms),
            "symptom_terms": list(query_terms.symptom_terms),
            "environment_terms": list(query_terms.environment_terms),
            "config_terms": list(query_terms.config_terms),
            "exact_terms": list(query_terms.exact_terms),
            "rare_anchor_terms": list(query_terms.rare_anchor_terms),
            "ignored_weak_terms": list(query_terms.ignored_weak_terms),
            "strongest_evidence_terms": list(query_terms.strongest_evidence_terms),
            "weights": {
                key: {
                    "class": value.frequency_class,
                    "weight": value.weight,
                    "document_frequency": value.document_frequency,
                    "chunk_frequency": value.chunk_frequency,
                    "term_type_guess": value.term_type_guess,
                }
                for key, value in query_terms.weights.items()
            },
        }


async def _workspace_id(supabase: Any, workspace_name: str) -> str | None:
    rows = await supabase.select(
        "workspaces",
        params={"select": "id", "name": f"eq.{workspace_name}", "limit": "1"},
    )
    if not rows:
        return None
    return str(rows[0]["id"])


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _load_settings() -> Any:
    try:
        from app.config import get_settings

        return get_settings()
    except ModuleNotFoundError as exc:
        if exc.name != "pydantic_settings":
            raise
        return SimpleNamespace(
            supabase_url=os.getenv("SUPABASE_URL", ""),
            supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            ollama_embedding_model=os.getenv("OLLAMA_EMBEDDING_MODEL", "BAAI/bge-m3"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"),
            embedding_dim=int(os.getenv("EMBEDDING_DIM", "1024")),
        )


def main() -> None:
    """CLI entry point."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
