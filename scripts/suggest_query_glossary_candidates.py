"""Suggest query glossary candidates from read-only runtime data."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.db.supabase_client import SupabaseClient, SupabaseRequestError  # noqa: E402
from app.logging_config import _redact_secrets  # noqa: E402
from app.rag.glossary_candidates import (  # noqa: E402
    GlossaryCandidateReport,
    discover_glossary_candidates,
    format_glossary_candidate_report,
)
from app.rag.query_enrichment import DEFAULT_QUERY_GLOSSARY_CONFIG, QueryGlossaryConfig, QueryGlossaryConfigError, load_query_glossary_config  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Read-only discovery of suggested query glossary candidates.",
    )
    parser.add_argument("--workspace", default="team", help="Workspace name to inspect when --workspace-id is not provided.")
    parser.add_argument("--workspace-id", help="Workspace UUID to inspect directly.")
    parser.add_argument("--limit", type=int, default=30, help="Maximum candidates to print.")
    parser.add_argument("--term-limit", type=int, default=500, help="Maximum term_statistics rows to read.")
    parser.add_argument("--evidence-limit", type=int, default=80, help="Maximum recent evidence_logs rows to read.")
    parser.add_argument("--document-limit", type=int, default=500, help="Maximum active documents to inspect.")
    parser.add_argument("--section-limit", type=int, default=500, help="Maximum active sections to inspect.")
    parser.add_argument("--chunk-limit", type=int, default=500, help="Maximum active chunks to inspect.")
    parser.add_argument("--config", type=Path, default=DEFAULT_QUERY_GLOSSARY_CONFIG, help="Seed query glossary YAML path.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--output", type=Path, help="Optional report path under tmp/ or reports/.")
    return parser.parse_args()


async def main_async() -> int:
    """Run read-only candidate discovery."""
    args = parse_args()
    warnings: list[str] = []
    glossary = _load_glossary(args.config, warnings)
    settings = get_settings()
    workspace_label = args.workspace_id or args.workspace

    if _missing(getattr(settings, "supabase_url", "")) or _missing(getattr(settings, "supabase_service_role_key", "")):
        warnings.append("runtime data unavailable: missing Supabase settings")
        report = _with_warnings(
            discover_glossary_candidates(workspace=workspace_label, existing_glossary=glossary, limit=args.limit),
            warnings,
        )
        _emit_report(report, json_output=args.json, output=args.output)
        return 2

    try:
        async with SupabaseClient(settings) as client:
            workspace_id = args.workspace_id or await _resolve_workspace_id(client, args.workspace)
            if not workspace_id:
                warnings.append(f"workspace not found: {args.workspace}")
                report = _with_warnings(
                    discover_glossary_candidates(workspace=workspace_label, existing_glossary=glossary, limit=args.limit),
                    warnings,
                )
                _emit_report(report, json_output=args.json, output=args.output)
                return 2
            runtime_rows = await _load_runtime_rows(client, workspace_id, args, warnings)
    except Exception as exc:  # noqa: BLE001 - CLI should return a readable setup/runtime problem
        warnings.append("runtime data unavailable: " + _safe_error(exc))
        report = _with_warnings(
            discover_glossary_candidates(workspace=workspace_label, existing_glossary=glossary, limit=args.limit),
            warnings,
        )
        _emit_report(report, json_output=args.json, output=args.output)
        return 2

    report = _with_warnings(
        discover_glossary_candidates(
            workspace=workspace_label,
            existing_glossary=glossary,
            term_statistics=runtime_rows["term_statistics"],
            evidence_logs=runtime_rows["evidence_logs"],
            documents=runtime_rows["documents"],
            document_cards=runtime_rows["document_cards"],
            sections=runtime_rows["sections"],
            chunks=runtime_rows["chunks"],
            limit=args.limit,
        ),
        warnings,
    )
    _emit_report(report, json_output=args.json, output=args.output)
    return 0


def _load_glossary(path: Path, warnings: list[str]) -> QueryGlossaryConfig | None:
    try:
        return load_query_glossary_config(path)
    except QueryGlossaryConfigError as exc:
        warnings.append("existing glossary unavailable: " + _safe_error(exc))
        return None


async def _resolve_workspace_id(client: SupabaseClient, workspace: str) -> str | None:
    rows = await client.select(
        "workspaces",
        params={"select": "id", "name": f"eq.{workspace}", "limit": "1"},
    )
    if not rows:
        return None
    return str(rows[0].get("id") or "") or None


async def _load_runtime_rows(
    client: SupabaseClient,
    workspace_id: str,
    args: argparse.Namespace,
    warnings: list[str],
) -> dict[str, list[dict[str, Any]]]:
    term_statistics = await _safe_select(
        client,
        "term_statistics",
        params={
            "select": "term,normalized_term,document_frequency,chunk_frequency,course_frequency,examples,term_type_guess,metadata",
            "workspace_id": f"eq.{workspace_id}",
            "order": "document_frequency.asc,chunk_frequency.asc",
            "limit": str(max(args.term_limit, 0)),
        },
        warnings=warnings,
    )
    evidence_logs = await _safe_select(
        client,
        "evidence_logs",
        params={
            "select": "question,question_analysis,evidence_pack,final_sources,created_at",
            "workspace_id": f"eq.{workspace_id}",
            "order": "created_at.desc",
            "limit": str(max(args.evidence_limit, 0)),
        },
        warnings=warnings,
    )
    documents = await _safe_select(
        client,
        "documents",
        params={
            "select": "id,source_type,filename,document_key,title,course,module,lesson,metadata,updated_at",
            "workspace_id": f"eq.{workspace_id}",
            "status": "eq.active",
            "order": "updated_at.desc",
            "limit": str(max(args.document_limit, 0)),
        },
        warnings=warnings,
    )
    document_ids = [str(row.get("id") or "") for row in documents if row.get("id")]
    document_cards = await _select_for_documents(
        client,
        "document_cards",
        document_ids,
        select="document_id,summary,topics,questions_answered,entities,task_types,metadata",
        limit=args.document_limit,
        warnings=warnings,
    )
    sections = await _select_for_documents(
        client,
        "sections",
        document_ids,
        select="id,document_id,heading,summary,metadata",
        limit=args.section_limit,
        warnings=warnings,
    )
    chunks = await _select_for_documents(
        client,
        "chunks",
        document_ids,
        select="id,document_id,chunk_index,heading,content,metadata",
        limit=args.chunk_limit,
        warnings=warnings,
    )
    return {
        "term_statistics": term_statistics,
        "evidence_logs": evidence_logs,
        "documents": documents,
        "document_cards": document_cards,
        "sections": sections,
        "chunks": chunks,
    }


async def _select_for_documents(
    client: SupabaseClient,
    table: str,
    document_ids: list[str],
    *,
    select: str,
    limit: int,
    warnings: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    remaining = max(limit, 0)
    if remaining == 0:
        return rows
    for group in _batches([item for item in document_ids if item], 30):
        if remaining <= 0:
            break
        batch_rows = await _safe_select(
            client,
            table,
            params={
                "select": select,
                "document_id": f"in.({','.join(group)})",
                "limit": str(remaining),
            },
            warnings=warnings,
        )
        rows.extend(batch_rows)
        remaining -= len(batch_rows)
    return rows


async def _safe_select(
    client: SupabaseClient,
    table: str,
    *,
    params: dict[str, Any],
    warnings: list[str],
) -> list[dict[str, Any]]:
    try:
        return await client.select(table, params=params)
    except SupabaseRequestError as exc:
        if exc.is_missing_relation:
            warnings.append(f"{table} unavailable: missing relation or schema cache")
            return []
        raise


def _emit_report(report: GlossaryCandidateReport, *, json_output: bool, output: Path | None) -> None:
    text = json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n" if json_output else format_glossary_candidate_report(report)
    print(text, end="")
    if output is not None:
        safe_path = _safe_output_path(output)
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        safe_path.write_text(text, encoding="utf-8")


def _safe_output_path(path: Path) -> Path:
    candidate = path if path.is_absolute() else ROOT / path
    resolved = candidate.resolve()
    allowed_roots = ((ROOT / "tmp").resolve(), (ROOT / "reports").resolve())
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        raise SystemExit("Output path must be under tmp/ or reports/.")
    return resolved


def _with_warnings(report: GlossaryCandidateReport, warnings: list[str]) -> GlossaryCandidateReport:
    return replace(report, warnings=tuple(dict.fromkeys(warnings)))


def _batches(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _missing(value: object) -> bool:
    text = str(value or "").strip()
    return not text or text.startswith("replace_with") or "your-project-ref" in text


def _safe_error(exc: BaseException) -> str:
    return _redact_secrets(str(exc) or exc.__class__.__name__).replace("\n", " ")[:500]


def main() -> None:
    """CLI entry point."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
