"""Evaluation report helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.eval.runner import EvalRun


def write_report_files(report: EvalRun, report_dir: Path) -> tuple[Path, Path, Path, Path]:
    """Write timestamped and latest JSON/Markdown reports."""
    report_dir.mkdir(parents=True, exist_ok=True)
    latest_json = report_dir / "latest.json"
    latest_md = report_dir / "latest.md"
    timestamp_json = report_dir / f"{report.run_id}.json"
    timestamp_md = report_dir / f"{report.run_id}.md"

    data = report.to_dict()
    markdown = render_markdown_report(data)
    for path in (latest_json, timestamp_json):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for path in (latest_md, timestamp_md):
        path.write_text(markdown, encoding="utf-8")
    return latest_json, latest_md, timestamp_json, timestamp_md


def render_markdown_report(report: dict[str, Any]) -> str:
    """Render a compact Markdown report."""
    summary = report.get("summary", {})
    lines = [
        "# RAG V2 Eval Report",
        "",
        f"- run_id: `{report.get('run_id', '')}`",
        f"- runner_mode: `{report.get('runner_mode', '')}`",
        f"- cases: {summary.get('total_cases', 0)}",
        f"- passed: {summary.get('passed_cases', 0)}",
        f"- failed: {summary.get('failed_cases', 0)}",
        f"- not_run: {summary.get('not_run_cases', 0)}",
        f"- average_final_score: {summary.get('average_final_score', 0)}",
        "",
        "| case | category | score | mode | issues |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for result in report.get("case_results", []):
        case = result.get("case", {})
        prediction = result.get("prediction", {})
        metrics = result.get("metrics", {})
        issues = ", ".join(result.get("issues", [])) or "-"
        lines.append(
            "| {case_id} | {category} | {score:.3f} | {mode} | {issues} |".format(
                case_id=case.get("id", ""),
                category=case.get("category", ""),
                score=float(metrics.get("final_score", 0.0)),
                mode=prediction.get("answer_mode", ""),
                issues=issues,
            )
        )
    return "\n".join(lines) + "\n"


def load_report(path: Path) -> dict[str, Any]:
    """Load an eval report JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def compare_eval_reports(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Compare two eval reports and return detected regressions."""
    baseline_cases = _case_results_by_id(baseline)
    current_cases = _case_results_by_id(current)
    regressions: list[dict[str, Any]] = []

    for case_id, current_result in current_cases.items():
        baseline_result = baseline_cases.get(case_id, {})
        regressions.extend(_case_regressions(case_id, baseline_result, current_result))

    summary = {
        "baseline_run_id": baseline.get("run_id"),
        "current_run_id": current.get("run_id"),
        "regression_count": len(regressions),
    }
    return {"summary": summary, "regressions": regressions}


def render_compare_markdown(comparison: dict[str, Any]) -> str:
    """Render comparison output as Markdown."""
    summary = comparison.get("summary", {})
    lines = [
        "# RAG V2 Eval Comparison",
        "",
        f"- baseline: `{summary.get('baseline_run_id', '')}`",
        f"- current: `{summary.get('current_run_id', '')}`",
        f"- regressions: {summary.get('regression_count', 0)}",
        "",
    ]
    regressions = comparison.get("regressions", [])
    if not regressions:
        lines.append("No regressions detected.")
        return "\n".join(lines) + "\n"
    lines.extend(["| case | type | detail |", "| --- | --- | --- |"])
    for regression in regressions:
        lines.append(
            "| {case_id} | {kind} | {detail} |".format(
                case_id=regression.get("case_id", ""),
                kind=regression.get("type", ""),
                detail=str(regression.get("detail", "")).replace("|", "\\|"),
            )
        )
    return "\n".join(lines) + "\n"


def _case_results_by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for result in report.get("case_results", []):
        case_id = str(result.get("case", {}).get("id") or result.get("prediction", {}).get("case_id") or "")
        if case_id:
            results[case_id] = result
    return results


def _case_regressions(
    case_id: str,
    baseline_result: dict[str, Any],
    current_result: dict[str, Any],
) -> list[dict[str, Any]]:
    regressions: list[dict[str, Any]] = []
    baseline_checks = baseline_result.get("metrics", {}).get("checks", {})
    current_checks = current_result.get("metrics", {}).get("checks", {})
    baseline_metrics = baseline_result.get("metrics", {})
    current_metrics = current_result.get("metrics", {})
    case = current_result.get("case", {})
    prediction = current_result.get("prediction", {})

    if baseline_checks.get("expected_document_hits") and not current_checks.get("expected_document_hits"):
        regressions.append(_regression(case_id, "expected_document_missing", "expected document disappeared"))

    if current_checks.get("forbidden_document_hits"):
        regressions.append(
            _regression(
                case_id,
                "forbidden_document_present",
                ", ".join(current_checks.get("forbidden_document_hits", [])),
            )
        )

    if not current_checks.get("source_count_ok", True):
        regressions.append(
            _regression(
                case_id,
                "source_count_above_limit",
                f"{current_checks.get('source_count')} > {case.get('expected_source_count_max')}",
            )
        )

    if not current_checks.get("answer_mode_matches", True):
        regressions.append(
            _regression(
                case_id,
                "answer_mode_mismatch",
                f"{prediction.get('answer_mode')} != {case.get('expected_answer_mode')}",
            )
        )

    if current_checks.get("sources_with_missing_data"):
        regressions.append(
            _regression(case_id, "sources_for_missing_data", "sources appeared for ask_for_missing_data")
        )

    if current_checks.get("used_discarded_candidates"):
        regressions.append(_regression(case_id, "discarded_candidate_used", "answer used discarded candidate"))

    baseline_score = float(baseline_metrics.get("final_score", 0.0))
    current_score = float(current_metrics.get("final_score", 0.0))
    if baseline_score - current_score >= 0.5:
        regressions.append(
            _regression(
                case_id,
                "score_drop",
                f"{baseline_score:.3f} -> {current_score:.3f}",
            )
        )

    return regressions


def _regression(case_id: str, kind: str, detail: str) -> dict[str, Any]:
    return {"case_id": case_id, "type": kind, "detail": detail}
