"""Run the Phase 7C-A no-write answer-quality baseline."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.rag.quality_harness import (
    HARNESS_SCHEMA_VERSION,
    AnswerQualityBaseline,
    AnswerQualityCase,
    AnswerQualityCaseResult,
    baseline_from_results,
    build_answer_quality_runtime_from_settings,
    discover_dynamic_cases,
    fixed_answer_quality_cases,
    load_existing_case_results,
    run_answer_quality_case,
    save_baseline_atomic,
)

DYNAMIC_CASE_IDS = (
    "uploaded_material_only_auto",
    "mixed_course_service_auto",
    "archived_exclusion",
    "vision_optional",
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    fixed_cases = fixed_answer_quality_cases()
    all_known_ids = [case.case_id for case in fixed_cases] + list(DYNAMIC_CASE_IDS)
    if args.list_cases:
        _print_cases(fixed_cases)
        return 0

    selected_ids = args.case or all_known_ids
    unknown = sorted(set(selected_ids) - set(all_known_ids))
    if unknown:
        parser.error("unknown case id(s): " + ", ".join(unknown))

    if not args.confirm_read_only_production:
        _print_plan(selected_ids, args)
        return 0

    if not args.output:
        parser.error("--output is required when --confirm-read-only-production is used")

    output_path = Path(args.output)
    try:
        return _run_confirmed(selected_ids=selected_ids, output_path=output_path, resume=args.resume, answer_mode=args.answer_mode)
    except Exception as exc:  # noqa: BLE001 - CLI should report harness/runtime failure clearly
        print(f"harness_failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a no-write answer-quality baseline using production reads, local embeddings, "
            "and configured answer models only when explicitly confirmed."
        )
    )
    parser.add_argument("--list-cases", action="store_true", help="List baseline cases without network/model access.")
    parser.add_argument("--case", action="append", default=[], help="Run one case id. May be repeated.")
    parser.add_argument("--output", help="Path to the external UTF-8 JSON baseline artifact.")
    parser.add_argument("--resume", action="store_true", help="Reuse completed case results already present in --output.")
    parser.add_argument("--answer-mode", default="cheap", choices=("cheap", "free", "quality"), help="Model routing answer mode.")
    parser.add_argument(
        "--confirm-read-only-production",
        action="store_true",
        help="Actually perform read-only Supabase requests, embeddings, and model calls.",
    )
    return parser


def _print_cases(fixed_cases: list[AnswerQualityCase]) -> None:
    print(f"schema_version: {HARNESS_SCHEMA_VERSION}")
    for case in fixed_cases:
        print(f"{case.case_id}\t{case.case_type}\t{case.question}")
    for case_id in DYNAMIC_CASE_IDS:
        print(f"{case_id}\tdynamic\t(discovered after confirmation)")


def _print_plan(selected_ids: list[str], args: argparse.Namespace) -> None:
    print("Plan only. No Supabase requests, embeddings, model calls, or Telegram calls were made.")
    print(f"schema_version: {HARNESS_SCHEMA_VERSION}")
    print(f"answer_mode: {args.answer_mode}")
    print("selected_cases:")
    for case_id in selected_ids:
        print(f"- {case_id}")
    if args.output:
        print(f"output: {args.output}")
    else:
        print("output: not set; pass --output with an external path before confirmed run")
    print("To run: add --confirm-read-only-production.")


def _run_confirmed(*, selected_ids: list[str], output_path: Path, resume: bool, answer_mode: str) -> int:
    settings = get_settings()
    runtime = build_answer_quality_runtime_from_settings(settings, answer_mode=answer_mode)
    completed: list[AnswerQualityCaseResult] = load_existing_case_results(output_path) if resume else []
    completed_ids = {case.case_id for case in completed}

    async def _run() -> AnswerQualityBaseline:
        cases_by_id = {case.case_id: case for case in fixed_answer_quality_cases()}
        if set(selected_ids) & set(DYNAMIC_CASE_IDS):
            dynamic_cases = await discover_dynamic_cases(runtime.resources.supabase, runtime.workspace_id)
            cases_by_id.update({case.case_id: case for case in dynamic_cases})

        results = list(completed)
        try:
            for case_id in selected_ids:
                if resume and case_id in completed_ids:
                    print(f"resume: keeping completed case {case_id}")
                    continue
                case = cases_by_id[case_id]
                print(f"running: {case.case_id}")
                result = await run_answer_quality_case(runtime, case)
                results.append(result)
                baseline = baseline_from_results(
                    case_results=results,
                    git_sha=_git_sha(),
                    workspace_id=runtime.workspace_id,
                    safety_state=runtime.resources.supabase.safety.snapshot(),
                )
                save_baseline_atomic(output_path, baseline)
                print(
                    "completed: "
                    f"{result.case_id} outcome={result.outcome} "
                    f"writes={baseline.supabase_write_attempts} "
                    f"unknown_rpc={baseline.non_allowlisted_rpc_attempts}"
                )
            return baseline_from_results(
                case_results=results,
                git_sha=_git_sha(),
                workspace_id=runtime.workspace_id,
                safety_state=runtime.resources.supabase.safety.snapshot(),
            )
        finally:
            await runtime.close()

    baseline = _run_coroutine(_run())
    save_baseline_atomic(output_path, baseline)
    print(f"baseline_saved: {output_path}")
    print(f"overall_classification: {baseline.overall_classification}")
    print(f"primary_blocker: {baseline.primary_blocker}")
    print(f"supabase_write_attempts: {baseline.supabase_write_attempts}")
    return 0


def _run_coroutine(coro: object) -> AnswerQualityBaseline:
    import asyncio

    return asyncio.run(coro)  # type: ignore[arg-type,return-value]


def _git_sha() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
    return result.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
