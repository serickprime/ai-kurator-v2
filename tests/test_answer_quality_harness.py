import asyncio
import json
from pathlib import Path

from app.config import Settings
from app.rag.quality_harness import (
    AnswerQualityBaseline,
    AnswerQualityCase,
    AnswerQualityCaseResult,
    CaseChecks,
    DocumentSummary,
    ReadOnlySupabaseClient,
    ReadOnlyViolation,
    analyze_case_result,
    baseline_from_results,
    build_answer_quality_runtime_from_settings,
    classify_baseline,
    dataclass_to_sanitized_dict,
    hydrate_documents_for_result,
    load_existing_case_results,
    save_baseline_atomic,
)
from app.rag.types import AnswerStatus, PipelineResult, SourceRef, VerificationReport


class FakeSupabase:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.rows: list[dict[str, object]] = []
        self.closed = False

    async def select(self, table: str, params: dict[str, object] | None = None) -> list[dict[str, object]]:
        del params
        self.calls.append(("select", table))
        return self.rows

    async def rpc(self, function_name: str, payload: dict[str, object] | None = None) -> list[dict[str, object]]:
        del payload
        self.calls.append(("rpc", function_name))
        return [{"ok": True}]

    async def insert(self, table: str, payload: object) -> list[dict[str, object]]:
        self.calls.append(("insert", table))
        return []

    async def update(self, table: str, payload: object, params: object) -> list[dict[str, object]]:
        self.calls.append(("update", table))
        return []

    async def delete(self, table: str, params: object) -> list[dict[str, object]]:
        self.calls.append(("delete", table))
        return []

    async def close(self) -> None:
        self.closed = True


def test_read_only_adapter_allows_select_and_approved_rpc() -> None:
    fake = FakeSupabase()
    client = ReadOnlySupabaseClient(fake)

    assert asyncio.run(client.select("documents")) == []
    assert asyncio.run(client.rpc("match_document_cards", {"x": 1})) == [{"ok": True}]

    assert fake.calls == [("select", "documents"), ("rpc", "match_document_cards")]
    assert client.safety.allowed_selects == 1
    assert client.safety.allowed_rpc_calls == 1
    assert client.safety.write_attempts == 0


def test_read_only_adapter_blocks_insert_update_delete_without_delegating() -> None:
    fake = FakeSupabase()
    client = ReadOnlySupabaseClient(fake)

    for call in (
        lambda: client.insert("documents", {}),
        lambda: client.update("documents", {}, {}),
        lambda: client.delete("documents", {}),
    ):
        try:
            asyncio.run(call())
        except ReadOnlyViolation:
            pass
        else:
            raise AssertionError("write operation was not blocked")

    assert fake.calls == []
    assert client.safety.write_attempts == 3
    assert client.safety.blocked_write_calls == 3


def test_unknown_rpc_is_blocked_and_not_delegated() -> None:
    fake = FakeSupabase()
    client = ReadOnlySupabaseClient(fake)

    try:
        asyncio.run(client.rpc("refresh_term_statistics", {"p_workspace_id": "x"}))
    except ReadOnlyViolation:
        pass
    else:
        raise AssertionError("unknown RPC was not blocked")

    assert fake.calls == []
    assert client.safety.write_attempts == 1
    assert client.safety.non_allowlisted_rpc_attempts == 1


def test_generic_write_like_methods_are_blocked() -> None:
    fake = FakeSupabase()
    client = ReadOnlySupabaseClient(fake)

    for call in (lambda: client.upsert("documents", {}), lambda: client.post("/rest/v1/documents")):
        try:
            asyncio.run(call())
        except ReadOnlyViolation:
            pass
        else:
            raise AssertionError("write-like operation was not blocked")

    assert fake.calls == []
    assert client.safety.write_attempts == 2


def test_quality_runtime_uses_logger_none_and_no_evidence_log_repository() -> None:
    runtime = build_answer_quality_runtime_from_settings(_complete_settings())

    assert runtime.pipeline._logger is None
    assert runtime.resources.supabase.safety.write_attempts == 0

    asyncio.run(runtime.close())


def test_telegram_is_not_imported_by_harness_runtime() -> None:
    import app.rag.quality_harness as quality_harness

    assert "telegram" not in quality_harness.__dict__


def test_case_result_sanitizes_secrets_and_truncates_evidence_preview() -> None:
    case = AnswerQualityCase(
        case_id="telegram_docs",
        question="как отправить сообщение через Telegram Bot API?",
        case_type="official_docs",
        expected_terms=("sendMessage",),
    )
    result = _pipeline_result(
        answer="Use sendMessage. secret sk-or-abcdefghijklmnopqrstuvwxyz",
        accepted_text="sendMessage " + ("x" * 500),
    )
    hydration = {"doc-1": _doc("Telegram Bot API", "external_docs", "active", "telegram")}

    analyzed = analyze_case_result(
        case=case,
        result=result,
        hydration=hydration,
        safety_state={"write_attempts": 0},
    )

    assert "<secret-redacted>" in analyzed.final_answer
    assert len(str(analyzed.accepted_evidence_summary[0]["preview"])) <= 300
    assert analyzed.checks.no_raw_uuid_in_answer


def test_archived_exclusion_failure_is_reported() -> None:
    case = AnswerQualityCase(case_id="archived_exclusion", question="q", case_type="archived_exclusion")
    result = _pipeline_result(answer="Supported answer.", accepted_text="supported")
    hydration = {"doc-1": _doc("Old doc", "external_docs", "archived", "telegram")}

    analyzed = analyze_case_result(case=case, result=result, hydration=hydration, safety_state={})

    assert analyzed.outcome == "FAIL"
    assert any("archived" in failure for failure in analyzed.failures)


def test_source_type_hydration_maps_external_and_uploaded() -> None:
    fake = FakeSupabase()
    fake.rows = [
        {
            "id": "doc-1",
            "title": "API docs",
            "source_type": "external_docs",
            "status": "active",
            "version": 2,
            "course": "",
            "metadata": {"source_name": "telegram"},
        }
    ]
    client = ReadOnlySupabaseClient(fake)
    result = _pipeline_result(answer="Supported answer.", accepted_text="supported")

    hydrated = asyncio.run(hydrate_documents_for_result(client, "workspace", result))

    assert hydrated["doc-1"].source_class == "external_docs"
    assert hydrated["doc-1"].source_name == "telegram"


def test_mixed_source_pass_and_fail_classification() -> None:
    case = AnswerQualityCase(
        case_id="mixed_course_service_auto",
        question="q",
        case_type="mixed_course_service",
        expected_source_types=("uploaded_or_local", "external_docs"),
        requires_mixed_sources=True,
    )
    result = _pipeline_result(answer="Supported answer.", accepted_text="supported", mixed=True)
    pass_result = analyze_case_result(
        case=case,
        result=result,
        hydration={
            "doc-1": _doc("Lesson", "upload", "active", ""),
            "doc-2": _doc("Docs", "external_docs", "active", "telegram"),
        },
        safety_state={},
    )
    fail_result = analyze_case_result(
        case=case,
        result=result,
        hydration={"doc-1": _doc("Lesson", "upload", "active", "")},
        safety_state={},
    )

    assert pass_result.outcome == "PASS"
    assert fail_result.outcome == "FAIL"


def test_ambiguous_and_out_of_base_classification() -> None:
    ambiguous = AnswerQualityCase(
        case_id="ambiguous_service",
        question="q",
        case_type="ambiguous_service",
        expected_limitation="service_ambiguous",
    )
    out_of_base = AnswerQualityCase(
        case_id="out_of_base",
        question="q",
        case_type="out_of_base",
        allowed_statuses=("insufficient_evidence", "needs_clarification"),
    )

    ambiguous_result = analyze_case_result(
        case=ambiguous,
        result=_pipeline_result(answer="Supported answer.", accepted_text="supported"),
        hydration={"doc-1": _doc("Docs", "external_docs", "active", "telegram")},
        safety_state={},
    )
    out_of_base_result = analyze_case_result(
        case=out_of_base,
        result=_pipeline_result(answer="Your balance is 100.", accepted_text="supported"),
        hydration={"doc-1": _doc("Docs", "external_docs", "active", "telegram")},
        safety_state={},
    )

    assert ambiguous_result.outcome == "WARN"
    assert out_of_base_result.outcome == "FAIL"


def test_followup_limitation_is_not_primary_phase7c_blocker() -> None:
    followup = AnswerQualityCaseResult(
        case_id="followup_without_memory",
        question="q",
        outcome="FAIL",
        failures=["expected limitation was not handled"],
    )

    overall, primary, next_phase = classify_baseline([followup])

    assert overall == "baseline_pass"
    assert primary == "no_blocking_functional_gap"
    assert "Phase 8A" in next_phase


def test_atomic_output_and_resume_behavior(tmp_path: Path) -> None:
    baseline = AnswerQualityBaseline(
        schema_version="test",
        generated_at="2026-07-13T00:00:00+00:00",
        git_sha="abc",
        workspace="workspace",
        evidence_logging_disabled=True,
        telegram_sending_disabled=True,
        supabase_write_attempts=0,
        blocked_write_calls=0,
        non_allowlisted_rpc_attempts=0,
        case_results=[AnswerQualityCaseResult(case_id="telegram_docs", question="q", outcome="PASS")],
        overall_classification="baseline_pass",
        primary_blocker="no_blocking_functional_gap",
        recommended_next_phase="Phase 8A",
    )
    path = tmp_path / "baseline.json"

    save_baseline_atomic(path, baseline)
    loaded = load_existing_case_results(path)

    assert path.exists()
    assert not list(tmp_path.glob("*.tmp"))
    assert loaded[0].case_id == "telegram_docs"


def test_secret_like_values_are_removed_from_report_dict() -> None:
    baseline = AnswerQualityBaseline(
        schema_version="test",
        generated_at="now",
        git_sha="abc",
        workspace="workspace",
        evidence_logging_disabled=True,
        telegram_sending_disabled=True,
        supabase_write_attempts=0,
        blocked_write_calls=0,
        non_allowlisted_rpc_attempts=0,
        case_results=[
            AnswerQualityCaseResult(
                case_id="case",
                question="q",
                outcome="PASS",
                final_answer="Bearer abcdefghijklmnopqrstuvwxyz",
            )
        ],
        overall_classification="baseline_pass",
        primary_blocker="no_blocking_functional_gap",
        recommended_next_phase="Phase 8A",
    )

    payload = json.dumps(dataclass_to_sanitized_dict(baseline), ensure_ascii=False)

    assert "Bearer abcdefghijklmnopqrstuvwxyz" not in payload
    assert "<secret-redacted>" in payload


def test_product_fail_does_not_require_harness_process_failure() -> None:
    case_result = AnswerQualityCaseResult(
        case_id="telegram_docs",
        question="q",
        outcome="FAIL",
        failures=["expected source class was not selected"],
    )

    baseline = baseline_from_results(
        case_results=[case_result],
        git_sha="abc",
        workspace_id="workspace",
        safety_state={"write_attempts": 0, "blocked_write_calls": 0, "non_allowlisted_rpc_attempts": 0},
    )

    assert baseline.overall_classification == "functional_blocker_found"
    assert baseline.primary_blocker == "explicit_service_routing_gap"
    assert baseline.supabase_write_attempts == 0


def _complete_settings() -> Settings:
    return Settings(
        _env_file=None,
        telegram_bot_token="",
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-role-test",
        default_workspace_id="00000000-0000-0000-0000-000000000001",
        openrouter_api_key="openrouter-test",
        openrouter_default_model="openai/gpt-4.1-mini",
        embedding_provider="local",
        embedding_model="BAAI/bge-m3",
        embedding_dim=1024,
        rag_pipeline_version="v2",
    )


def _pipeline_result(
    *,
    answer: str,
    accepted_text: str,
    status: AnswerStatus = AnswerStatus.ANSWERED,
    mixed: bool = False,
) -> PipelineResult:
    sources = [SourceRef(document_id="doc-1", document_title="Doc", evidence_id="ev-1")]
    selected_documents = [{"document_id": "doc-1"}]
    accepted_evidence = [
        {
            "evidence_id": "ev-1",
            "document_id": "doc-1",
            "document_title": "Doc",
            "text": accepted_text,
            "score": 0.9,
        }
    ]
    if mixed:
        sources.append(SourceRef(document_id="doc-2", document_title="Docs", evidence_id="ev-2"))
        selected_documents.append({"document_id": "doc-2"})
        accepted_evidence.append(
            {
                "evidence_id": "ev-2",
                "document_id": "doc-2",
                "document_title": "Docs",
                "text": accepted_text,
                "score": 0.8,
            }
        )
    return PipelineResult(
        answer=answer,
        status=status,
        sources=tuple(sources),
        verification=VerificationReport(is_supported=True),
        debug={
            "selected_documents": selected_documents,
            "accepted_evidence": accepted_evidence,
            "query_plan": {"domain_hint": "telegram"},
            "answer_mode": "answer_from_materials",
        },
    )


def _doc(title: str, source_type: str, status: str, source_name: str) -> DocumentSummary:
    return DocumentSummary(
        document_id_hash="hash",
        title=title,
        source_type=source_type,
        source_class="external_docs" if source_type == "external_docs" else "uploaded_or_local",
        status=status,
        version=1,
        source_name=source_name,
    )
