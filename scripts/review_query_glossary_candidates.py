"""Owner/admin review and apply flow for query glossary candidates."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.db.supabase_client import SupabaseClient  # noqa: E402
from app.rag.glossary_candidates import discover_glossary_candidates  # noqa: E402
from app.rag.glossary_review import (  # noqa: E402
    DEFAULT_REVIEWED_GLOSSARY_OUTPUT,
    GlossaryReviewError,
    apply_reviewed_candidates,
    build_apply_plan,
    dump_review_file,
    format_apply_plan,
    load_review_file,
    review_file_from_report,
    validate_review_file,
)
from app.rag.query_enrichment import DEFAULT_QUERY_GLOSSARY_CONFIG, QueryGlossaryConfigError, load_query_glossary_config  # noqa: E402
from scripts.suggest_query_glossary_candidates import (  # noqa: E402
    _load_glossary,
    _load_runtime_rows,
    _missing,
    _resolve_workspace_id,
    _safe_error,
    _with_warnings,
)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Review and apply query glossary candidates after owner/admin approval.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export-review", help="Export Phase 4A candidates to an owner-review file.")
    export.add_argument("--workspace", default="team", help="Workspace name to inspect when --workspace-id is not provided.")
    export.add_argument("--workspace-id", help="Workspace UUID to inspect directly.")
    export.add_argument("--limit", type=int, default=30, help="Maximum candidates to export.")
    export.add_argument("--term-limit", type=int, default=500, help="Maximum term_statistics rows to read.")
    export.add_argument("--evidence-limit", type=int, default=80, help="Maximum recent evidence_logs rows to read.")
    export.add_argument("--document-limit", type=int, default=500, help="Maximum active documents to inspect.")
    export.add_argument("--section-limit", type=int, default=500, help="Maximum active sections to inspect.")
    export.add_argument("--chunk-limit", type=int, default=500, help="Maximum active chunks to inspect.")
    export.add_argument("--config", type=Path, default=DEFAULT_QUERY_GLOSSARY_CONFIG, help="Seed query glossary YAML path.")
    export.add_argument("--output", type=Path, default=Path("reports/glossary_candidates_review.yaml"), help="Review file path under tmp/ or reports/.")

    validate = subparsers.add_parser("validate-review", help="Validate an owner-edited review file.")
    validate.add_argument("--review-file", type=Path, required=True, help="Review YAML file to validate.")

    plan = subparsers.add_parser("plan-apply", help="Print a dry-run apply plan from a review file.")
    plan.add_argument("--review-file", type=Path, required=True, help="Review YAML file to plan from.")
    plan.add_argument("--config", type=Path, default=DEFAULT_QUERY_GLOSSARY_CONFIG, help="Existing query glossary YAML path.")
    plan.add_argument("--output", type=Path, help="Optional apply plan path under tmp/ or reports/.")

    apply = subparsers.add_parser("apply-reviewed", help="Write reviewed glossary output, or config only with explicit confirmation.")
    apply.add_argument("--review-file", type=Path, required=True, help="Review YAML file to apply.")
    apply.add_argument("--config", type=Path, default=DEFAULT_QUERY_GLOSSARY_CONFIG, help="Existing query glossary YAML path.")
    apply.add_argument("--output", type=Path, default=DEFAULT_REVIEWED_GLOSSARY_OUTPUT, help="Reviewed output path under tmp/ or reports/.")
    apply.add_argument("--write-config", action="store_true", help="Write directly to config/query_glossary.yaml.")
    apply.add_argument("--confirm-reviewed-apply", action="store_true", help="Required together with --write-config.")
    return parser.parse_args()


async def main_async() -> int:
    """Run the selected command."""
    args = parse_args()
    try:
        if args.command == "export-review":
            return await _export_review(args)
        if args.command == "validate-review":
            return _validate_review(args)
        if args.command == "plan-apply":
            return _plan_apply(args)
        if args.command == "apply-reviewed":
            return _apply_reviewed(args)
    except (GlossaryReviewError, QueryGlossaryConfigError) as exc:
        print(f"error: {_safe_error(exc)}", file=sys.stderr)
        return 2
    raise AssertionError(f"Unhandled command: {args.command}")


async def _export_review(args: argparse.Namespace) -> int:
    report, status = await _candidate_report_from_runtime(args)
    review = review_file_from_report(report)
    output = _safe_output_path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(dump_review_file(review), encoding="utf-8")
    print("Glossary Candidate Review Export")
    print("")
    print("- mode: owner-review-required")
    print(f"- output: {_display_path(output)}")
    print(f"- candidates: {len(review.candidates)}")
    print("- config/query_glossary.yaml: unchanged")
    print("- Supabase writes: none")
    print("- next action: edit owner_decision fields manually, then run validate-review and plan-apply")
    if review.warnings:
        for warning in review.warnings:
            print(f"- warning: {warning}")
    return status


def _validate_review(args: argparse.Namespace) -> int:
    review = load_review_file(args.review_file)
    warnings = validate_review_file(review)
    decisions = _decision_counts(review)
    print("Glossary Candidate Review Validation")
    print("")
    print("- status: valid")
    print(f"- review_file: {args.review_file}")
    print(f"- candidates: {len(review.candidates)}")
    for decision, count in decisions.items():
        print(f"- {decision}: {count}")
    for warning in warnings:
        print(f"- warning: {warning}")
    return 0


def _plan_apply(args: argparse.Namespace) -> int:
    review = load_review_file(args.review_file)
    glossary = load_query_glossary_config(args.config)
    plan = build_apply_plan(review, glossary)
    text = format_apply_plan(plan)
    print(text, end="")
    if args.output:
        output = _safe_output_path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    return 0


def _apply_reviewed(args: argparse.Namespace) -> int:
    review = load_review_file(args.review_file)
    output = None if args.write_config else _safe_output_path(args.output)
    plan, written = apply_reviewed_candidates(
        review=review,
        config_path=args.config,
        output_path=output,
        write_config=args.write_config,
        confirm_reviewed_apply=args.confirm_reviewed_apply,
    )
    print(format_apply_plan(replace(plan, warnings=(*plan.warnings, "apply-reviewed completed"))), end="")
    if written is None:
        print("- output: none")
    else:
        print(f"- output: {_display_path(written)}")
    if args.write_config:
        print("- config write: direct write confirmed")
    else:
        print("- config/query_glossary.yaml: unchanged")
    return 0


async def _candidate_report_from_runtime(args: argparse.Namespace):
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
        return report, 2

    try:
        async with SupabaseClient(settings) as client:
            workspace_id = args.workspace_id or await _resolve_workspace_id(client, args.workspace)
            if not workspace_id:
                warnings.append(f"workspace not found: {args.workspace}")
                report = _with_warnings(
                    discover_glossary_candidates(workspace=workspace_label, existing_glossary=glossary, limit=args.limit),
                    warnings,
                )
                return report, 2
            runtime_rows = await _load_runtime_rows(client, workspace_id, args, warnings)
    except Exception as exc:  # noqa: BLE001 - CLI should return a readable runtime/setup problem
        warnings.append("runtime data unavailable: " + _safe_error(exc))
        report = _with_warnings(
            discover_glossary_candidates(workspace=workspace_label, existing_glossary=glossary, limit=args.limit),
            warnings,
        )
        return report, 2

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
    return report, 0


def _safe_output_path(path: Path) -> Path:
    candidate = path if path.is_absolute() else ROOT / path
    resolved = candidate.resolve()
    allowed_roots = ((ROOT / "tmp").resolve(), (ROOT / "reports").resolve())
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        raise GlossaryReviewError("Output path must be under tmp/ or reports/.")
    return resolved


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path)


def _decision_counts(review) -> dict[str, int]:
    result = {decision: 0 for decision in ("pending", "approved", "edited", "rejected")}
    for candidate in review.candidates:
        result[candidate.owner_decision] = result.get(candidate.owner_decision, 0) + 1
    return result


def main() -> None:
    """CLI entry point."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
