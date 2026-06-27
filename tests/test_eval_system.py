import json

from app.eval.metrics import score_eval_case
from app.eval.reports import compare_eval_reports
from app.eval.runner import EvalCase, EvalPrediction, run_eval


def _case() -> EvalCase:
    return EvalCase(
        id="n8n_local_install",
        category="document_routing",
        question="как установить н8н локально?",
        expected_documents=("установка n8n", "localhost"),
        forbidden_documents=("YooMoney",),
        expected_answer_terms=("локально", "localhost"),
        forbidden_answer_terms=("YooKassa",),
        expected_answer_mode="answer_from_materials",
        expected_source_count_max=2,
        must_not_use_discarded_candidates=True,
        expected_supported_points=("где открыть интерфейс",),
        requires_sources=True,
    )


def test_eval_scores_documents_sources_evidence_and_answer_terms() -> None:
    prediction = EvalPrediction(
        case_id="n8n_local_install",
        answer="n8n запускается локально. Где открыть интерфейс: localhost.",
        answer_mode="answer_from_materials",
        documents=("Материал: установка n8n локально localhost",),
        evidence_items=("Где открыть интерфейс: localhost после запуска n8n.",),
        sources=("Установка n8n, localhost",),
        status="ok",
    )

    score = score_eval_case(_case(), prediction)

    assert score.document_precision > 0
    assert score.source_precision == 1.0
    assert score.answer_term_score == 1.0
    assert score.forbidden_leakage == 0.0
    assert score.final_score > 0.75


def test_eval_penalizes_forbidden_and_discarded_leakage() -> None:
    prediction = EvalPrediction(
        case_id="n8n_local_install",
        answer="Ответ про YooKassa из discarded материала.",
        answer_mode="answer_from_materials",
        documents=("YooMoney платежи",),
        evidence_items=("YooMoney hash",),
        sources=("YooMoney",),
        discarded_candidates=("YooMoney",),
        status="ok",
    )

    score = score_eval_case(_case(), prediction)

    assert score.forbidden_leakage == 1.0
    assert score.checks["used_discarded_candidates"] is True
    assert score.final_score < 0.5


def test_runner_marks_cases_not_run_without_predictions(tmp_path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps([_case_dict()], ensure_ascii=False), encoding="utf-8")

    report = run_eval(cases_path=cases_path)

    assert report.summary["total_cases"] == 1
    assert report.summary["not_run_cases"] == 1
    assert report.case_results[0].issues


def test_compare_reports_detects_required_regressions() -> None:
    baseline = {
        "run_id": "baseline",
        "case_results": [
            {
                "case": _case_dict(),
                "prediction": {"case_id": "n8n_local_install", "answer_mode": "answer_from_materials"},
                "metrics": {
                    "final_score": 0.95,
                    "checks": {
                        "expected_document_hits": ["установка n8n"],
                        "forbidden_document_hits": [],
                        "source_count_ok": True,
                        "answer_mode_matches": True,
                        "sources_with_missing_data": False,
                        "used_discarded_candidates": False,
                    },
                },
            }
        ],
    }
    current = {
        "run_id": "current",
        "case_results": [
            {
                "case": _case_dict(),
                "prediction": {"case_id": "n8n_local_install", "answer_mode": "ask_for_missing_data"},
                "metrics": {
                    "final_score": 0.2,
                    "checks": {
                        "expected_document_hits": [],
                        "forbidden_document_hits": ["YooMoney"],
                        "source_count": 3,
                        "source_count_ok": False,
                        "answer_mode_matches": False,
                        "sources_with_missing_data": True,
                        "used_discarded_candidates": True,
                    },
                },
            }
        ],
    }

    comparison = compare_eval_reports(baseline, current)
    regression_types = {item["type"] for item in comparison["regressions"]}

    assert "expected_document_missing" in regression_types
    assert "forbidden_document_present" in regression_types
    assert "source_count_above_limit" in regression_types
    assert "answer_mode_mismatch" in regression_types
    assert "sources_for_missing_data" in regression_types
    assert "discarded_candidate_used" in regression_types
    assert "score_drop" in regression_types


def _case_dict() -> dict[str, object]:
    return {
        "id": "n8n_local_install",
        "category": "document_routing",
        "question": "как установить н8н локально?",
        "expected_documents": ["установка n8n", "localhost"],
        "forbidden_documents": ["YooMoney"],
        "expected_answer_terms": ["локально", "localhost"],
        "forbidden_answer_terms": ["YooKassa"],
        "expected_answer_mode": "answer_from_materials",
        "expected_source_count_max": 2,
        "must_not_use_discarded_candidates": True,
        "expected_supported_points": ["где открыть интерфейс"],
        "requires_sources": True,
    }
