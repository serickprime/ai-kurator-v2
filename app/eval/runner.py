"""Evaluation runner for evidence-first RAG."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.eval.metrics import EvalScore, score_eval_case


CASE_FIELDS = (
    "id",
    "category",
    "question",
    "expected_documents",
    "forbidden_documents",
    "expected_answer_terms",
    "forbidden_answer_terms",
    "expected_answer_mode",
    "expected_source_count_max",
    "must_not_use_discarded_candidates",
    "expected_supported_points",
    "requires_sources",
)


@dataclass(frozen=True)
class EvalCase:
    """One RAG v2 evaluation case."""

    id: str
    category: str
    question: str
    expected_documents: tuple[str, ...]
    forbidden_documents: tuple[str, ...]
    expected_answer_terms: tuple[str, ...]
    forbidden_answer_terms: tuple[str, ...]
    expected_answer_mode: str
    expected_source_count_max: int
    must_not_use_discarded_candidates: bool
    expected_supported_points: tuple[str, ...]
    requires_sources: bool

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "EvalCase":
        """Build and validate an eval case from JSON."""
        missing = [field_name for field_name in CASE_FIELDS if field_name not in row]
        if missing:
            raise ValueError(f"Eval case {row.get('id', '<unknown>')} is missing fields: {', '.join(missing)}")
        return cls(
            id=str(row["id"]),
            category=str(row["category"]),
            question=str(row["question"]),
            expected_documents=_tuple_str(row["expected_documents"]),
            forbidden_documents=_tuple_str(row["forbidden_documents"]),
            expected_answer_terms=_tuple_str(row["expected_answer_terms"]),
            forbidden_answer_terms=_tuple_str(row["forbidden_answer_terms"]),
            expected_answer_mode=str(row["expected_answer_mode"]),
            expected_source_count_max=int(row["expected_source_count_max"]),
            must_not_use_discarded_candidates=bool(row["must_not_use_discarded_candidates"]),
            expected_supported_points=_tuple_str(row["expected_supported_points"]),
            requires_sources=bool(row["requires_sources"]),
        )


@dataclass(frozen=True)
class EvalPrediction:
    """Normalized RAG output used by the evaluator."""

    case_id: str
    answer: str = ""
    answer_mode: str = ""
    documents: tuple[str, ...] = ()
    chunks: tuple[str, ...] = ()
    evidence_items: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    discarded_candidates: tuple[str, ...] = ()
    used_discarded_candidates: bool = False
    status: str = "not_run"
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def empty(cls, case_id: str) -> "EvalPrediction":
        """Return an explicit not-run prediction."""
        return cls(case_id=case_id)

    @classmethod
    def from_dict(cls, case_id: str, row: dict[str, Any]) -> "EvalPrediction":
        """Normalize a loose prediction/debug record from JSON."""
        evidence_pack = _dict(row.get("evidence_pack"))
        answer = str(row.get("answer") or row.get("final_answer") or row.get("text") or "")
        answer_mode = str(
            row.get("answer_mode")
            or evidence_pack.get("answer_mode")
            or row.get("mode")
            or ""
        )
        documents = _collect_texts(
            row.get("documents"),
            row.get("selected_documents"),
            row.get("document_candidates"),
            row.get("routed_documents"),
        )
        chunks = _collect_texts(row.get("chunks"), row.get("retrieved_chunks"), row.get("chunk_candidates"))
        evidence_items = _collect_texts(row.get("evidence_items"), row.get("evidence"), evidence_pack.get("items"))
        sources = _collect_texts(row.get("sources"), row.get("final_sources"), evidence_pack.get("source_matches"))
        discarded = _collect_texts(row.get("discarded_candidates"), row.get("discarded_documents"))
        used_discarded = bool(row.get("used_discarded_candidates") or row.get("source_leakage"))
        status = str(row.get("status") or "ok")
        return cls(
            case_id=case_id,
            answer=answer,
            answer_mode=answer_mode,
            documents=documents,
            chunks=chunks,
            evidence_items=evidence_items,
            sources=sources,
            discarded_candidates=discarded,
            used_discarded_candidates=used_discarded,
            status=status,
            raw=row,
        )


@dataclass(frozen=True)
class EvalCaseResult:
    """Scored case result."""

    case: EvalCase
    prediction: EvalPrediction
    metrics: EvalScore
    passed: bool
    issues: tuple[str, ...]


@dataclass(frozen=True)
class EvalRun:
    """Full eval run report."""

    run_id: str
    created_at: str
    cases_path: str
    prediction_source: str | None
    runner_mode: str
    summary: dict[str, Any]
    case_results: tuple[EvalCaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        """Serialize the report to JSON-compatible data."""
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "cases_path": self.cases_path,
            "prediction_source": self.prediction_source,
            "runner_mode": self.runner_mode,
            "summary": self.summary,
            "case_results": [
                {
                    "case": _case_dict(result.case),
                    "prediction": _prediction_dict(result.prediction),
                    "metrics": asdict(result.metrics),
                    "passed": result.passed,
                    "issues": list(result.issues),
                }
                for result in self.case_results
            ],
        }


def load_cases(path: Path) -> tuple[EvalCase, ...]:
    """Load and validate eval cases."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Eval cases file must contain a JSON list")
    return tuple(EvalCase.from_dict(_dict(row)) for row in data)


def load_predictions(path: Path | None) -> dict[str, EvalPrediction]:
    """Load normalized predictions keyed by case id."""
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = _prediction_rows(data)
    predictions: dict[str, EvalPrediction] = {}
    for case_id, row in rows.items():
        predictions[case_id] = EvalPrediction.from_dict(case_id, row)
    return predictions


def run_eval(
    *,
    cases_path: Path = Path("app/eval/cases.json"),
    predictions_path: Path | None = None,
) -> EvalRun:
    """Run evaluation cases against normalized predictions."""
    cases = load_cases(cases_path)
    predictions = load_predictions(predictions_path)
    runner_mode = "predictions" if predictions_path else "not_run"

    results: list[EvalCaseResult] = []
    for case in cases:
        prediction = predictions.get(case.id, EvalPrediction.empty(case.id))
        metrics = score_eval_case(case, prediction)
        issues = _issues(case, prediction, metrics)
        results.append(
            EvalCaseResult(
                case=case,
                prediction=prediction,
                metrics=metrics,
                passed=not issues and metrics.final_score >= 0.75,
                issues=tuple(issues),
            )
        )

    created_at = datetime.now(timezone.utc)
    return EvalRun(
        run_id=created_at.strftime("%Y%m%d_%H%M%S"),
        created_at=created_at.isoformat(),
        cases_path=str(cases_path),
        prediction_source=str(predictions_path) if predictions_path else None,
        runner_mode=runner_mode,
        summary=_summary(results),
        case_results=tuple(results),
    )


def _summary(results: list[EvalCaseResult]) -> dict[str, Any]:
    total = len(results)
    not_run = sum(1 for result in results if result.prediction.status == "not_run")
    passed = sum(1 for result in results if result.passed)
    avg_score = sum(result.metrics.final_score for result in results) / total if total else 0.0
    return {
        "total_cases": total,
        "passed_cases": passed,
        "failed_cases": total - passed,
        "not_run_cases": not_run,
        "average_final_score": round(avg_score, 4),
    }


def _issues(case: EvalCase, prediction: EvalPrediction, metrics: EvalScore) -> list[str]:
    issues: list[str] = []
    checks = metrics.checks
    if prediction.status == "not_run":
        issues.append("case_not_run")
    if not checks["answer_mode_matches"]:
        issues.append("answer_mode_mismatch")
    if checks["forbidden_document_hits"]:
        issues.append("forbidden_document_used")
    if checks["forbidden_answer_hits"]:
        issues.append("forbidden_answer_term_used")
    if not checks["source_count_ok"]:
        issues.append("source_count_above_limit")
    if not checks["sources_required_ok"]:
        issues.append("missing_required_sources")
    if checks["sources_with_missing_data"]:
        issues.append("sources_present_for_missing_data")
    if not checks["discarded_ok"]:
        issues.append("discarded_candidate_used")
    if case.expected_documents and not checks["expected_document_hits"]:
        issues.append("expected_document_missing")
    return issues


def _prediction_rows(data: Any) -> dict[str, dict[str, Any]]:
    if isinstance(data, list):
        return {
            str(_dict(row).get("case_id") or _dict(row).get("id")): _dict(row)
            for row in data
            if _dict(row).get("case_id") or _dict(row).get("id")
        }
    if isinstance(data, dict):
        if isinstance(data.get("predictions"), list):
            return _prediction_rows(data["predictions"])
        if isinstance(data.get("case_results"), list):
            rows: dict[str, dict[str, Any]] = {}
            for item in data["case_results"]:
                item_dict = _dict(item)
                case = _dict(item_dict.get("case"))
                prediction = _dict(item_dict.get("prediction"))
                case_id = str(case.get("id") or prediction.get("case_id") or "")
                if case_id:
                    rows[case_id] = prediction
            return rows
        return {str(key): _dict(value) for key, value in data.items() if isinstance(value, dict)}
    raise ValueError("Predictions JSON must be a list, mapping, or eval report")


def _collect_texts(*values: Any) -> tuple[str, ...]:
    texts: list[str] = []
    for value in values:
        texts.extend(_texts_from_value(value))
    return tuple(_dedupe(texts))


def _texts_from_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, list | tuple):
        texts: list[str] = []
        for item in value:
            texts.extend(_texts_from_value(item))
        return texts
    if isinstance(value, dict):
        preferred = (
            "document_id",
            "document_title",
            "title",
            "filename",
            "course",
            "lesson",
            "heading",
            "locator",
            "text",
            "content",
            "summary",
            "source",
        )
        texts = [str(value[key]) for key in preferred if value.get(key) is not None]
        if texts:
            return [" | ".join(texts)]
        return [json.dumps(value, ensure_ascii=False, sort_keys=True)]
    return [str(value)]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = str(item).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _tuple_str(value: Any) -> tuple[str, ...]:
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value if str(item).strip())
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return ()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _case_dict(case: EvalCase) -> dict[str, Any]:
    row = asdict(case)
    for key in (
        "expected_documents",
        "forbidden_documents",
        "expected_answer_terms",
        "forbidden_answer_terms",
        "expected_supported_points",
    ):
        row[key] = list(row[key])
    return row


def _prediction_dict(prediction: EvalPrediction) -> dict[str, Any]:
    row = asdict(prediction)
    for key in ("documents", "chunks", "evidence_items", "sources", "discarded_candidates"):
        row[key] = list(row[key])
    return row
