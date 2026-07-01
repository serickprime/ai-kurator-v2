import asyncio
import json
from pathlib import Path

from scripts.evaluate_retrieval_simple_synthetic import (
    DEFAULT_CASES_PATH,
    DEFAULT_MATERIALS_DIR,
    calculate_metrics,
    case_result_passes,
    load_cases,
    run_benchmark,
    write_reports,
)


def test_synthetic_materials_exist_and_have_fact_ids() -> None:
    files = sorted(DEFAULT_MATERIALS_DIR.glob("*.md"))

    assert len(files) == 10
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "## Контрольные факты" in text
        assert "FACT-ID:" in text


def test_simple_synthetic_eval_cases_have_expected_fields() -> None:
    cases = load_cases(DEFAULT_CASES_PATH)

    assert len(cases) >= 35
    assert all(case.expected_document is not None for case in cases)
    assert any(case.forbidden_documents for case in cases)
    assert any(case.expected_answer_mode == "out_of_base" for case in cases)
    assert any(case.expected_answer_mode == "ask_for_missing_data" for case in cases)


def test_simple_synthetic_report_is_created(tmp_path: Path) -> None:
    report = asyncio.run(run_benchmark(question="как хранить бананы?"))
    latest_json, latest_md = write_reports(report, tmp_path)

    assert latest_json.exists()
    assert latest_md.exists()
    assert json.loads(latest_json.read_text(encoding="utf-8"))["metrics"]["total_cases"] == 1
    assert "| id | question | expected doc | top doc | doc pass |" in latest_md.read_text(encoding="utf-8")


def test_evidence_leakage_is_fail_but_raw_leakage_is_allowed() -> None:
    raw_only = {
        "document_pass": True,
        "missing_fact_ids": [],
        "raw_forbidden_documents": ["cactus_care.md"],
        "evidence_forbidden_documents": [],
        "actual_answer_mode": "answer_from_materials",
        "expected_answer_mode": "answer_from_materials",
        "evidence_pack_items": [{"document": "lemon_tree_care.md"}],
    }
    evidence_leak = {
        **raw_only,
        "evidence_forbidden_documents": ["cactus_care.md"],
        "evidence_pack_items": [{"document": "cactus_care.md"}],
    }

    assert case_result_passes(raw_only)
    assert not case_result_passes(evidence_leak)
    assert calculate_metrics([evidence_leak])["forbidden_document_leakage"] == 1.0


def test_out_of_base_question_gets_no_evidence_pack_sources() -> None:
    report = asyncio.run(run_benchmark(question="как хранить бананы?"))
    result = report["case_results"][0]

    assert result["expected_answer_mode"] == "out_of_base"
    assert result["actual_answer_mode"] == "out_of_base"
    assert result["evidence_pack_items"] == []
    assert result["result"] == "pass"


def test_incomplete_question_asks_for_missing_data() -> None:
    report = asyncio.run(run_benchmark(question="как это хранить?"))
    result = report["case_results"][0]

    assert result["expected_answer_mode"] == "ask_for_missing_data"
    assert result["actual_answer_mode"] == "ask_for_missing_data"
    assert result["evidence_pack_items"] == []
    assert result["result"] == "pass"


def test_simple_synthetic_quality_gate() -> None:
    report = asyncio.run(run_benchmark())
    metrics = report["metrics"]

    assert metrics["forbidden_document_leakage"] == 0.0
    assert metrics["document_top1_accuracy"] >= 0.95
    assert metrics["chunk_fact_recall"] >= 0.95
    assert metrics["answer_mode_accuracy"] == 1.0
    assert all(result["result"] == "pass" for result in report["case_results"])
