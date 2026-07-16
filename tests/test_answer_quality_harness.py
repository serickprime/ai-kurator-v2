import asyncio
import json
import sys
import unicodedata
from pathlib import Path

import pytest

from app.config import Settings
from app.db.supabase_client import SupabaseRequestError
from app.rag.quality_harness import (
    HARNESS_SCHEMA_VERSION,
    AnswerQualityBaseline,
    AnswerQualityCase,
    AnswerQualityCaseResult,
    CaseChecks,
    DocumentSummary,
    HarnessOperationState,
    ProductionSafetyTelemetry,
    ReadOnlySupabaseClient,
    ReadOnlyViolation,
    TableStateSnapshot,
    analyze_case_result,
    baseline_from_results,
    build_production_safety_telemetry,
    build_answer_quality_runtime_from_settings,
    capture_production_safety_snapshot,
    classify_baseline,
    dataclass_to_sanitized_dict,
    fixed_answer_quality_cases,
    hydrate_documents_for_result,
    load_existing_case_results,
    run_answer_quality_case,
    save_baseline_atomic,
)
from app.rag.types import AnswerStatus, PipelineResult, SourceRef, VerificationReport


UNICODE_PD_CHARACTERS = tuple(
    chr(codepoint)
    for codepoint in range(sys.maxunicode + 1)
    if unicodedata.category(chr(codepoint)) == "Pd"
)
UNICODE_PD_MIXED_RUNS = tuple(
    UNICODE_PD_CHARACTERS[index]
    + UNICODE_PD_CHARACTERS[(index + 1) % len(UNICODE_PD_CHARACTERS)]
    for index in range(len(UNICODE_PD_CHARACTERS))
)
DASH_RUN_SAMPLES = (
    "--",
    "\ufe63\uff0d",
    "-\u2014",
    "\u2212\u2013",
    "\ufe58\ufe63\uff0d",
)


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


class SnapshotSupabase(FakeSupabase):
    def __init__(
        self,
        table_rows: dict[str, list[dict[str, object]]],
        *,
        table_errors: dict[str, Exception] | None = None,
    ) -> None:
        super().__init__()
        self.table_rows = table_rows
        self.table_errors = table_errors or {}

    async def select(self, table: str, params: dict[str, object] | None = None) -> list[dict[str, object]]:
        self.calls.append(("select", table))
        if table in self.table_errors:
            raise self.table_errors[table]
        params = params or {}
        offset = int(params.get("offset") or 0)
        limit = int(params.get("limit") or 500)
        return self.table_rows.get(table, [])[offset : offset + limit]


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


def test_v3_safety_telemetry_contains_counters_and_rpc_counts_by_name() -> None:
    fake = FakeSupabase()
    client = ReadOnlySupabaseClient(fake)
    asyncio.run(client.select("documents"))
    asyncio.run(client.rpc("match_document_cards", {}))
    asyncio.run(client.rpc("match_document_cards", {}))
    asyncio.run(client.rpc("match_chunks_in_documents", {}))
    snapshots = _snapshot_set()

    telemetry = build_production_safety_telemetry(
        safety_state=client.safety.snapshot(),
        operation_state=HarnessOperationState(
            model_attempts=3,
            settings_loader_attempted=True,
            settings_loader_used=True,
        ),
        before_snapshots=snapshots,
        after_snapshots=snapshots,
    )

    payload = dataclass_to_sanitized_dict(telemetry)
    assert telemetry.safety_result == "PASS"
    assert telemetry.select_calls == 1
    assert telemetry.allowlisted_rpc_calls_total == 3
    assert telemetry.allowlisted_rpc_calls_by_name == {
        "match_chunks_in_documents": 1,
        "match_document_cards": 2,
    }
    assert telemetry.model_attempts == 3
    assert {
        "unknown_rpc_attempts",
        "supabase_write_attempts",
        "blocked_write_attempts",
        "evidence_log_write_attempts",
        "telegram_message_attempts",
        "external_docs_operation_attempts",
    }.issubset(payload)


@pytest.mark.parametrize(
    ("safety_state", "operation_state"),
    (
        (
            {"write_attempts": 1, "blocked_write_calls": 1},
            HarnessOperationState(settings_loader_attempted=True, settings_loader_used=True),
        ),
        (
            {"non_allowlisted_rpc_attempts": 1},
            HarnessOperationState(settings_loader_attempted=True, settings_loader_used=True),
        ),
        (
            {},
            HarnessOperationState(
                evidence_log_write_attempts=1,
                settings_loader_attempted=True,
                settings_loader_used=True,
            ),
        ),
        (
            {},
            HarnessOperationState(
                telegram_message_attempts=1,
                settings_loader_attempted=True,
                settings_loader_used=True,
            ),
        ),
    ),
)
def test_v3_safety_attempts_fail_closed(
    safety_state: dict[str, object],
    operation_state: HarnessOperationState,
) -> None:
    snapshots = _snapshot_set()
    telemetry = build_production_safety_telemetry(
        safety_state=safety_state,
        operation_state=operation_state,
        before_snapshots=snapshots,
        after_snapshots=snapshots,
    )

    assert telemetry.safety_result == "FAIL"


def test_v3_safety_snapshot_unchanged_changed_and_incomplete() -> None:
    before = _snapshot_set()
    unchanged = build_production_safety_telemetry(
        safety_state={},
        operation_state=HarnessOperationState(settings_loader_attempted=True, settings_loader_used=True),
        before_snapshots=before,
        after_snapshots=_snapshot_set(),
    )
    count_changed_after = _snapshot_set()
    count_changed_after["documents"] = TableStateSnapshot(
        row_count=2,
        safe_metadata_digest="a" * 64,
        snapshot_status="complete",
    )
    count_changed = build_production_safety_telemetry(
        safety_state={},
        operation_state=HarnessOperationState(settings_loader_attempted=True, settings_loader_used=True),
        before_snapshots=before,
        after_snapshots=count_changed_after,
    )
    digest_changed_after = _snapshot_set()
    digest_changed_after["chunks"] = TableStateSnapshot(
        row_count=1,
        safe_metadata_digest="b" * 64,
        snapshot_status="complete",
    )
    digest_changed = build_production_safety_telemetry(
        safety_state={},
        operation_state=HarnessOperationState(settings_loader_attempted=True, settings_loader_used=True),
        before_snapshots=before,
        after_snapshots=digest_changed_after,
    )
    missing_before = dict(before)
    missing_before.pop("messages")
    incomplete_before = build_production_safety_telemetry(
        safety_state={},
        operation_state=HarnessOperationState(settings_loader_attempted=True, settings_loader_used=True),
        before_snapshots=missing_before,
        after_snapshots=_snapshot_set(),
    )
    missing_after = _snapshot_set()
    missing_after.pop("sections")
    incomplete_after = build_production_safety_telemetry(
        safety_state={},
        operation_state=HarnessOperationState(settings_loader_attempted=True, settings_loader_used=True),
        before_snapshots=before,
        after_snapshots=missing_after,
    )

    assert unchanged.safety_result == "PASS"
    assert unchanged.documents_changed is False
    assert count_changed.documents_changed is True
    assert count_changed.safety_result == "FAIL"
    assert digest_changed.chunks_changed is True
    assert digest_changed.safety_result == "FAIL"
    assert incomplete_before.messages_changed == "unknown"
    assert incomplete_before.safety_result == "BLOCKED"
    assert incomplete_after.sections_changed == "unknown"
    assert incomplete_after.safety_result == "BLOCKED"


def test_v3_snapshot_artifact_contains_only_counts_digests_and_safe_errors() -> None:
    raw_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    secret_content = "private lesson content"
    fake = SnapshotSupabase(
        {
            "documents": [
                {
                    "id": raw_uuid,
                    "workspace_id": raw_uuid,
                    "version": 2,
                    "content_hash": "content-hash",
                    "title": secret_content,
                }
            ],
            "sections": [{"id": raw_uuid, "summary": secret_content}],
            "chunks": [{"id": raw_uuid, "content": secret_content}],
            "conversations": [{"id": raw_uuid, "summary": secret_content}],
            "messages": [{"id": raw_uuid, "content": secret_content}],
        },
        table_errors={
            "term_statistics": SupabaseRequestError(
                500,
                "provider details with internal request metadata",
                path="/rest/v1/term_statistics",
            )
        },
    )

    snapshots = asyncio.run(
        capture_production_safety_snapshot(ReadOnlySupabaseClient(fake))
    )
    payload = json.dumps(
        dataclass_to_sanitized_dict(snapshots),
        ensure_ascii=False,
        sort_keys=True,
    )

    assert raw_uuid not in payload
    assert secret_content not in payload
    assert "provider details" not in payload
    assert snapshots["documents"].row_count == 1
    assert snapshots["document_versions"].row_count == 1
    assert snapshots["document_versions"].source_relation == "documents"
    assert snapshots["term_statistics"].snapshot_status == "incomplete"
    assert snapshots["term_statistics"].error_code == "supabase_http_500"


def test_v3_env_and_secret_flags_are_independent() -> None:
    snapshots = _snapshot_set()
    telemetry = build_production_safety_telemetry(
        safety_state={},
        operation_state=HarnessOperationState(
            manual_env_access_performed=False,
            settings_loader_attempted=True,
            settings_loader_used=True,
            secret_values_rendered=False,
        ),
        before_snapshots=snapshots,
        after_snapshots=snapshots,
    )

    assert telemetry.manual_env_access_performed is False
    assert telemetry.settings_loader_used is True
    assert telemetry.secret_values_rendered is False
    assert dataclass_to_sanitized_dict(telemetry)["secret_values_rendered"] is False

    secret_failure = build_production_safety_telemetry(
        safety_state={},
        operation_state=HarnessOperationState(
            settings_loader_attempted=True,
            settings_loader_used=True,
            secret_values_rendered=True,
        ),
        before_snapshots=snapshots,
        after_snapshots=snapshots,
    )
    assert secret_failure.safety_result == "FAIL"


def test_quality_runtime_uses_logger_none_and_no_evidence_log_repository() -> None:
    runtime = build_answer_quality_runtime_from_settings(_complete_settings())

    assert runtime.pipeline._logger is None
    assert runtime.resources.supabase.safety.write_attempts == 0
    assert runtime.resources.operation_state.evidence_logging_disabled
    assert runtime.resources.operation_state.telegram_sending_disabled
    assert runtime.resources.operation_state.read_only_adapter_enabled

    asyncio.run(runtime.close())


def test_model_attempt_counter_wraps_actual_provider_calls() -> None:
    import app.rag.quality_harness as quality_harness

    class FakeModelClient:
        async def complete_text_with_model(
            self,
            model: str,
            messages: list[dict[str, str]],
        ) -> str:
            del model, messages
            return "ok"

        async def complete_vision_with_model(
            self,
            model: str,
            image_payload: object,
            prompt: str,
        ) -> str:
            del model, image_payload, prompt
            return "ok"

    state = HarnessOperationState()
    client = quality_harness._TelemetryModelClient(FakeModelClient(), state)

    assert asyncio.run(client.complete_text_with_model("provider/model", [])) == "ok"
    assert asyncio.run(client.complete_vision_with_model("provider/model", object(), "prompt")) == "ok"
    assert state.model_attempts == 2


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


def test_ambiguous_confident_service_answer_fails_and_cautious_answer_passes() -> None:
    ambiguous = AnswerQualityCase(
        case_id="ambiguous_service",
        question="q",
        case_type="ambiguous_service",
        service_expectation="forbid_confident",
        allowed_statuses=("insufficient_evidence", "needs_clarification"),
        allowed_answer_modes=("ask_for_missing_data", "out_of_base"),
        expected_limitation="service_ambiguous",
    )
    confident = analyze_case_result(
        case=ambiguous,
        result=_pipeline_result(
            answer="Configure the Supabase API.",
            accepted_text="Supabase API project URL and key.",
        ),
        hydration={"doc-1": _doc("Supabase Docs", "external_docs", "active", "supabase")},
        safety_state={},
    )
    cautious = analyze_case_result(
        case=ambiguous,
        result=_pipeline_result(
            answer="Уточните, какой сервис вы используете.",
            accepted_text="",
            status=AnswerStatus.NEEDS_CLARIFICATION,
            answer_mode="out_of_base",
            include_accepted=False,
            selected_document_ids=(),
        ),
        hydration={},
        safety_state={},
    )

    assert confident.outcome == "FAIL"
    assert not confident.checks.service_expectation_met
    assert "forbid_confident_service_violation" in confident.failure_codes
    assert confident.blocker_categories == ["ambiguous_service_routing_gap"]
    assert cautious.outcome == "PASS"
    assert cautious.checks.service_expectation_met


def test_wrong_service_cannot_pass_from_generic_answer_terms() -> None:
    case = AnswerQualityCase(
        case_id="openrouter_docs",
        question="q",
        case_type="official_docs",
        expected_service_ids=("openrouter",),
        service_expectation="required",
        required_evidence_term_groups=(("API",),),
        requires_accepted_evidence=True,
    )
    result = _pipeline_result(
        answer="Configure the API key and Authorization header.",
        accepted_text="Supabase API keys configure database access.",
    )

    analyzed = analyze_case_result(
        case=case,
        result=result,
        hydration={"doc-1": _doc("Supabase Docs", "external_docs", "active", "supabase")},
        safety_state={},
    )

    assert analyzed.outcome == "FAIL"
    assert not analyzed.checks.service_expectation_met
    assert analyzed.manual_review["correct_service_found"] is False
    assert "wrong_service_evidence" in analyzed.failure_codes
    assert "explicit_service_routing_gap" in analyzed.blocker_categories


def test_synthetic_result_without_actual_service_metadata_cannot_pass_service_case() -> None:
    case = AnswerQualityCase(
        case_id="openrouter_stub",
        question="q",
        case_type="official_docs",
        expected_service_ids=("openrouter",),
        service_expectation="required",
        required_evidence_term_groups=(("API key",),),
        requires_accepted_evidence=True,
    )
    analyzed = analyze_case_result(
        case=case,
        result=_pipeline_result(
            answer="Use the API key.",
            accepted_text="Use the API key.",
        ),
        hydration={"doc-1": _doc("Generic Docs", "external_docs", "active", "")},
        safety_state={},
    )

    assert analyzed.outcome == "FAIL"
    assert not analyzed.checks.service_expectation_met


def test_required_evidence_groups_require_every_group_but_accept_synonyms() -> None:
    case = AnswerQualityCase(
        case_id="grouped_terms",
        question="q",
        case_type="official_docs",
        required_evidence_term_groups=(
            ("HTTP Request", "HTTP Request node"),
            ("POST", "method", "headers", "body"),
        ),
        requires_accepted_evidence=True,
    )
    missing_group = analyze_case_result(
        case=case,
        result=_pipeline_result(
            answer="Use the HTTP Request node.",
            accepted_text="The HTTP Request node sends requests.",
        ),
        hydration={"doc-1": _doc("n8n Docs", "external_docs", "active", "n8n")},
        safety_state={},
    )
    synonym_match = analyze_case_result(
        case=case,
        result=_pipeline_result(
            answer="Use the node and configure headers.",
            accepted_text="The HTTP Request node supports headers.",
        ),
        hydration={"doc-1": _doc("n8n Docs", "external_docs", "active", "n8n")},
        safety_state={},
    )

    assert missing_group.outcome == "FAIL"
    assert not missing_group.checks.required_evidence_groups_met
    assert synonym_match.outcome == "PASS"
    assert synonym_match.checks.required_evidence_groups_met


def test_boundary_aware_word_matching_blocks_substring_false_pass() -> None:
    case = AnswerQualityCase(
        case_id="telegram_docs",
        question="q",
        case_type="official_docs",
        expected_service_ids=("telegram_bot_api",),
        service_expectation="required",
        required_evidence_term_groups=(("sendMessage",), ("chat_id",), ("text",)),
        requires_accepted_evidence=True,
    )
    analyzed = analyze_case_result(
        case=case,
        result=_pipeline_result(
            answer="Use sendMessage with chat_id.",
            accepted_text="Use sendMessage with chat_id in this context.",
        ),
        hydration={"doc-1": _doc("Telegram Bot API", "external_docs", "active", "telegram_bot_api")},
        safety_state={},
    )

    assert analyzed.outcome == "FAIL"
    assert not analyzed.checks.required_evidence_groups_met
    groups = analyzed.expectation_diagnostics["required_evidence_term_groups"]
    assert groups[2]["alternatives"] == ["text"]
    assert groups[2]["matched_terms"] == []


def test_boundary_aware_word_matching_accepts_exact_and_punctuation() -> None:
    case = AnswerQualityCase(
        case_id="word_boundary",
        question="q",
        case_type="official_docs",
        required_evidence_term_groups=(("text",),),
        requires_accepted_evidence=True,
    )

    for evidence in (
        "Use the text parameter.",
        'Use the "text" parameter.',
        "Use text, chat_id, and parse_mode.",
        "The field is text.",
    ):
        analyzed = analyze_case_result(
            case=case,
            result=_pipeline_result(answer="Supported answer.", accepted_text=evidence),
            hydration={"doc-1": _doc("Docs", "external_docs", "active", "telegram_bot_api")},
            safety_state={},
        )
        assert analyzed.outcome == "PASS"
        assert analyzed.checks.required_evidence_groups_met


def test_boundary_aware_word_matching_rejects_prefix_suffix_forms() -> None:
    case = AnswerQualityCase(
        case_id="word_boundary_negative",
        question="q",
        case_type="official_docs",
        required_evidence_term_groups=(("text",),),
        requires_accepted_evidence=True,
    )

    for evidence in ("plaintext", "textual", "subtext", "context"):
        analyzed = analyze_case_result(
            case=case,
            result=_pipeline_result(answer="Supported answer.", accepted_text=evidence),
            hydration={"doc-1": _doc("Docs", "external_docs", "active", "telegram_bot_api")},
            safety_state={},
        )
        assert analyzed.outcome == "FAIL"
        assert not analyzed.checks.required_evidence_groups_met


def test_boundary_aware_snake_case_identifier_matching() -> None:
    case = AnswerQualityCase(
        case_id="snake_case_boundary",
        question="q",
        case_type="official_docs",
        required_evidence_term_groups=(("chat_id",),),
        requires_accepted_evidence=True,
    )
    positive = analyze_case_result(
        case=case,
        result=_pipeline_result(answer="Supported answer.", accepted_text="Set the `chat_id` parameter."),
        hydration={"doc-1": _doc("Telegram Bot API", "external_docs", "active", "telegram_bot_api")},
        safety_state={},
    )
    assert positive.outcome == "PASS"

    for evidence in ("chat_identifier", "chat_ids", "my_chat_id_value"):
        analyzed = analyze_case_result(
            case=case,
            result=_pipeline_result(answer="Supported answer.", accepted_text=evidence),
            hydration={"doc-1": _doc("Telegram Bot API", "external_docs", "active", "telegram_bot_api")},
            safety_state={},
        )
        assert analyzed.outcome == "FAIL"
        assert not analyzed.checks.required_evidence_groups_met


def test_boundary_aware_camel_case_identifier_matching() -> None:
    case = AnswerQualityCase(
        case_id="camel_case_boundary",
        question="q",
        case_type="official_docs",
        required_evidence_term_groups=(("sendMessage",),),
        requires_accepted_evidence=True,
    )
    positive = analyze_case_result(
        case=case,
        result=_pipeline_result(answer="Supported answer.", accepted_text="Call `sendMessage` with chat_id."),
        hydration={"doc-1": _doc("Telegram Bot API", "external_docs", "active", "telegram_bot_api")},
        safety_state={},
    )
    assert positive.outcome == "PASS"

    for evidence in ("sendMessages", "resendMessage", "sendMessageBatch"):
        analyzed = analyze_case_result(
            case=case,
            result=_pipeline_result(answer="Supported answer.", accepted_text=evidence),
            hydration={"doc-1": _doc("Telegram Bot API", "external_docs", "active", "telegram_bot_api")},
            safety_state={},
        )
        assert analyzed.outcome == "FAIL"
        assert not analyzed.checks.required_evidence_groups_met


def test_boundary_aware_phrase_matching_requires_full_phrase() -> None:
    case = AnswerQualityCase(
        case_id="phrase_boundary",
        question="q",
        case_type="official_docs",
        required_evidence_term_groups=(("HTTP Request node",),),
        requires_accepted_evidence=True,
    )
    positive = analyze_case_result(
        case=case,
        result=_pipeline_result(
            answer="Supported answer.",
            accepted_text="Configure the HTTP   request NODE before sending data.",
        ),
        hydration={"doc-1": _doc("n8n Docs", "external_docs", "active", "n8n")},
        safety_state={},
    )
    negative = analyze_case_result(
        case=case,
        result=_pipeline_result(
            answer="Supported answer.",
            accepted_text="Use HTTP credentials on the workflow node.",
        ),
        hydration={"doc-1": _doc("n8n Docs", "external_docs", "active", "n8n")},
        safety_state={},
    )

    assert positive.outcome == "PASS"
    assert negative.outcome == "FAIL"


@pytest.mark.parametrize("dash", UNICODE_PD_CHARACTERS)
def test_boundary_aware_all_unicode_dash_punctuation_rejects_attached_suffix(dash: str) -> None:
    analyzed = _analyze_required_term("API key", f"Use API key{dash}v2.")
    assert analyzed.outcome == "FAIL", f"U+{ord(dash):04X}"
    assert not analyzed.checks.required_evidence_groups_met


@pytest.mark.parametrize("dash_run", DASH_RUN_SAMPLES)
def test_boundary_aware_dash_runs_reject_attached_suffixes(dash_run: str) -> None:
    cases = (
        ("API key", f"Use API key{dash_run}v2."),
        ("text", f"Use text{dash_run}beta."),
        ("chat_id", f"Use chat_id{dash_run}v2."),
        ("sendMessage", f"Use sendMessage{dash_run}beta."),
        ("/chat/completions", f"Use /chat/completions{dash_run}v2."),
    )

    for term, evidence in cases:
        analyzed = _analyze_required_term(term, evidence)
        assert analyzed.outcome == "FAIL", (term, evidence)
        assert not analyzed.checks.required_evidence_groups_met


@pytest.mark.parametrize("length", (2, 3, 4, 5))
def test_boundary_aware_dash_runs_of_any_length_reject_attached_suffix(length: int) -> None:
    analyzed = _analyze_required_term("API key", f"Use API key{'-' * length}v2.")
    assert analyzed.outcome == "FAIL"
    assert not analyzed.checks.required_evidence_groups_met


def test_boundary_aware_attached_suffixes_do_not_match_terms() -> None:
    cases = [
        ("/chat/completions", "Use /chat/completions-v2."),
        ("/chat/completions", "Use /chat/completions_v2."),
        ("/chat/completions", "Use /chat/completions2."),
        ("text", "Use text-beta."),
        ("chat_id", "Use chat_id-v2."),
        ("sendMessage", "Use sendMessage-beta."),
    ]

    for term, evidence in cases:
        analyzed = _analyze_required_term(term, evidence)
        assert analyzed.outcome == "FAIL", (term, evidence)
        assert not analyzed.checks.required_evidence_groups_met


def test_boundary_aware_paths_and_spaced_dashes_match() -> None:
    cases = (
        ("/chat/completions", "Use /chat/completions."),
        ("/chat/completions", "Use /chat/completions, then inspect the response."),
        ("API key", "Use API key - configure it in settings."),
        ("API key", "Use API key -- configure it in settings."),
        ("API key", "Use API key \ufe58 configure it in settings."),
        ("API key", "Use API key \ufe63 configure it in settings."),
        ("API key", "Use API key \uff0d configure it in settings."),
        ("API key", "Use API key \u2014 configure it in settings."),
        ("API key", "Use API key \u2014 \u2014 configure it in settings."),
    )

    for term, evidence in cases:
        analyzed = _analyze_required_term(term, evidence)
        assert analyzed.outcome == "PASS", (term, evidence)
        assert analyzed.checks.required_evidence_groups_met


@pytest.mark.parametrize("dash", UNICODE_PD_CHARACTERS)
def test_boundary_aware_all_unicode_dash_punctuation_rejects_attached_prefix(dash: str) -> None:
    analyzed = _analyze_required_term("v2", f"api{dash}v2")
    assert analyzed.outcome == "FAIL", f"U+{ord(dash):04X}"
    assert not analyzed.checks.required_evidence_groups_met


@pytest.mark.parametrize("dash_run", DASH_RUN_SAMPLES)
def test_boundary_aware_dash_runs_reject_attached_prefixes(dash_run: str) -> None:
    analyzed = _analyze_required_term("v2", f"api{dash_run}v2")
    assert analyzed.outcome == "FAIL", dash_run.encode("unicode_escape")
    assert not analyzed.checks.required_evidence_groups_met


@pytest.mark.parametrize("dash", UNICODE_PD_CHARACTERS)
def test_boundary_aware_all_unicode_dash_punctuation_matches_internal_dash(dash: str) -> None:
    analyzed = _analyze_required_term("alpha-beta", f"Use alpha{dash}beta in this example.")
    assert analyzed.outcome == "PASS", f"U+{ord(dash):04X}"
    assert analyzed.checks.required_evidence_groups_met


@pytest.mark.parametrize("dash_run", DASH_RUN_SAMPLES)
def test_boundary_aware_dash_runs_match_internal_dashes(dash_run: str) -> None:
    analyzed = _analyze_required_term("alpha-beta", f"Use alpha{dash_run}beta in this example.")
    assert analyzed.outcome == "PASS", dash_run.encode("unicode_escape")
    assert analyzed.checks.required_evidence_groups_met


@pytest.mark.parametrize("dash_run", UNICODE_PD_MIXED_RUNS)
def test_boundary_aware_mixed_unicode_pd_runs_share_dash_contract(dash_run: str) -> None:
    suffix = _analyze_required_term("API key", f"Use API key{dash_run}v2.")
    prefix = _analyze_required_term("v2", f"api{dash_run}v2")
    internal = _analyze_required_term("alpha-beta", f"Use alpha{dash_run}beta.")

    assert suffix.outcome == "FAIL", dash_run.encode("unicode_escape")
    assert prefix.outcome == "FAIL", dash_run.encode("unicode_escape")
    assert internal.outcome == "PASS", dash_run.encode("unicode_escape")


def test_boundary_aware_minus_sign_keeps_dash_contract() -> None:
    internal = _analyze_required_term("alpha-beta", "Use alpha\u2212beta in this example.")
    suffix = _analyze_required_term("API key", "Use API key\u2212v2.")
    prefix = _analyze_required_term("v2", "Use api\u2212v2.")

    assert internal.outcome == "PASS"
    assert suffix.outcome == "FAIL"
    assert prefix.outcome == "FAIL"


@pytest.mark.parametrize("dash_run", ("\u2212-", "-\u2212", "\u2212\u2013"))
def test_boundary_aware_minus_sign_mixed_runs_keep_dash_contract(dash_run: str) -> None:
    suffix = _analyze_required_term("API key", f"Use API key{dash_run}v2.")
    prefix = _analyze_required_term("v2", f"Use api{dash_run}v2.")
    internal = _analyze_required_term("alpha-beta", f"Use alpha{dash_run}beta.")

    assert suffix.outcome == "FAIL", dash_run.encode("unicode_escape")
    assert prefix.outcome == "FAIL", dash_run.encode("unicode_escape")
    assert internal.outcome == "PASS", dash_run.encode("unicode_escape")


@pytest.mark.parametrize("dash", ("\ufe58", "\ufe63", "\uff0d"))
def test_boundary_aware_small_and_fullwidth_dash_regressions(dash: str) -> None:
    internal = _analyze_required_term("alpha-beta", f"Use alpha{dash}beta.")
    suffix = _analyze_required_term("API key", f"Use API key{dash}v2.")

    assert internal.outcome == "PASS", f"U+{ord(dash):04X}"
    assert suffix.outcome == "FAIL", f"U+{ord(dash):04X}"


def test_boundary_aware_existing_adversarial_substrings_remain_rejected() -> None:
    cases = (
        ("text", "context"),
        ("key", "keyboard"),
        ("id", "identifier"),
        ("API key", "API keyboard"),
        ("sendMessage", "sendMessages"),
        ("chat_id", "chat_ids"),
        ("match_documents", "match_documents_v2"),
    )

    for term, evidence in cases:
        analyzed = _analyze_required_term(term, evidence)
        assert analyzed.outcome == "FAIL", (term, evidence)
        assert not analyzed.checks.required_evidence_groups_met


def test_selected_service_document_without_accepted_evidence_fails() -> None:
    case = AnswerQualityCase(
        case_id="openrouter_docs",
        question="q",
        case_type="official_docs",
        expected_service_ids=("openrouter",),
        service_expectation="required",
        required_evidence_term_groups=(("API key",),),
        requires_accepted_evidence=True,
    )
    result = _pipeline_result(
        answer="No supported fragment was found.",
        accepted_text="",
        status=AnswerStatus.NEEDS_CLARIFICATION,
        answer_mode="out_of_base",
        include_accepted=False,
        selected_document_ids=("doc-1",),
    )

    analyzed = analyze_case_result(
        case=case,
        result=result,
        hydration={"doc-1": _doc("OpenRouter Docs", "external_docs", "active", "openrouter")},
        safety_state={},
    )

    assert analyzed.outcome == "FAIL"
    assert not analyzed.checks.accepted_evidence_present
    assert not analyzed.checks.service_expectation_met


def test_answer_text_without_evidence_support_does_not_satisfy_required_group() -> None:
    case = AnswerQualityCase(
        case_id="answer_only",
        question="q",
        case_type="official_docs",
        required_evidence_term_groups=(("Authorization", "Bearer"),),
        requires_accepted_evidence=True,
    )
    analyzed = analyze_case_result(
        case=case,
        result=_pipeline_result(
            answer="Use the Authorization Bearer header.",
            accepted_text="This evidence only describes general API usage.",
        ),
        hydration={"doc-1": _doc("Docs", "external_docs", "active", "openrouter")},
        safety_state={},
    )

    assert analyzed.outcome == "FAIL"
    assert not analyzed.checks.required_evidence_groups_met


def test_correct_service_and_evidence_passes() -> None:
    case = AnswerQualityCase(
        case_id="openrouter_docs",
        question="q",
        case_type="official_docs",
        expected_service_ids=("openrouter",),
        service_expectation="required",
        expected_source_types=("external_docs",),
        required_evidence_term_groups=(
            ("API key", "API keys"),
            ("Authorization", "Bearer"),
        ),
        requires_accepted_evidence=True,
    )
    analyzed = analyze_case_result(
        case=case,
        result=_pipeline_result(
            answer="Use an API key as a Bearer token.",
            accepted_text="OpenRouter API keys are passed in the Authorization Bearer header.",
        ),
        hydration={"doc-1": _doc("OpenRouter Docs", "external_docs", "active", "openrouter")},
        safety_state={},
    )

    assert analyzed.outcome == "PASS"
    assert analyzed.checks.service_expectation_met
    assert analyzed.checks.required_evidence_groups_met
    assert analyzed.checks.required_source_types_present


def test_fixed_cases_keep_existing_term_expectations_with_boundary_matcher() -> None:
    cases = {case.case_id: case for case in fixed_answer_quality_cases()}
    samples = {
        "telegram_docs": (
            "Telegram Bot API",
            "telegram_bot_api",
            "Use sendMessage with chat_id and text.",
        ),
        "n8n_docs": (
            "n8n Docs",
            "n8n",
            "The HTTP Request node supports method, headers, and body.",
        ),
        "openrouter_docs": (
            "OpenRouter Docs",
            "openrouter",
            "OpenRouter API keys use the Authorization: Bearer header.",
        ),
        "supabase_docs": (
            "Supabase Docs",
            "supabase",
            "Supabase pgvector stores embeddings and match_documents enables vector search.",
        ),
    }

    for case_id, (title, service_id, evidence) in samples.items():
        analyzed = analyze_case_result(
            case=cases[case_id],
            result=_pipeline_result(answer="Supported answer.", accepted_text=evidence),
            hydration={"doc-1": _doc(title, "external_docs", "active", service_id)},
            safety_state={},
        )
        assert analyzed.outcome == "PASS"
        assert analyzed.checks.required_evidence_groups_met


def test_optional_evidence_group_produces_warn_without_failing_required_contract() -> None:
    case = AnswerQualityCase(
        case_id="optional_terms",
        question="q",
        case_type="official_docs",
        required_evidence_term_groups=(("API key",),),
        optional_evidence_term_groups=(("rate limits",),),
        requires_accepted_evidence=True,
    )
    analyzed = analyze_case_result(
        case=case,
        result=_pipeline_result(
            answer="Use the API key.",
            accepted_text="Configure the API key in the client.",
        ),
        hydration={"doc-1": _doc("Docs", "external_docs", "active", "openrouter")},
        safety_state={},
    )

    assert analyzed.outcome == "WARN"
    assert analyzed.checks.required_evidence_groups_met
    assert not analyzed.checks.optional_evidence_groups_met
    assert classify_baseline([analyzed])[0] == "baseline_pass"


def test_optional_evidence_group_uses_boundary_matching() -> None:
    case = AnswerQualityCase(
        case_id="optional_boundary",
        question="q",
        case_type="official_docs",
        required_evidence_term_groups=(("API key",),),
        optional_evidence_term_groups=(("text",),),
        requires_accepted_evidence=True,
    )
    analyzed = analyze_case_result(
        case=case,
        result=_pipeline_result(
            answer="Use the API key.",
            accepted_text="Configure the API key in this context.",
        ),
        hydration={"doc-1": _doc("Docs", "external_docs", "active", "openrouter")},
        safety_state={},
    )

    assert analyzed.outcome == "WARN"
    assert analyzed.checks.required_evidence_groups_met
    assert not analyzed.checks.optional_evidence_groups_met


def test_boundary_related_failure_affects_overall_classification() -> None:
    case = AnswerQualityCase(
        case_id="telegram_docs",
        question="q",
        case_type="official_docs",
        expected_service_ids=("telegram_bot_api",),
        service_expectation="required",
        required_evidence_term_groups=(("sendMessage",), ("chat_id",), ("text",)),
        requires_accepted_evidence=True,
    )
    analyzed = analyze_case_result(
        case=case,
        result=_pipeline_result(
            answer="Use sendMessage with chat_id.",
            accepted_text="Use sendMessage with chat_id in this context.",
        ),
        hydration={"doc-1": _doc("Telegram Bot API", "external_docs", "active", "telegram_bot_api")},
        safety_state={},
    )

    assert analyzed.outcome == "FAIL"
    assert classify_baseline([analyzed])[0] == "functional_blocker_found"


def test_blocked_and_skipped_fixtures_remain_explicit() -> None:
    class FailIfCalledPipeline:
        async def answer(self, *args: object, **kwargs: object) -> PipelineResult:
            del args, kwargs
            raise AssertionError("blocked or skipped fixture must not call the pipeline")

    class FakeRuntime:
        pipeline = FailIfCalledPipeline()

    blocked = asyncio.run(
        run_answer_quality_case(
            FakeRuntime(),  # type: ignore[arg-type]
            AnswerQualityCase(
                case_id="missing_fixture",
                question="",
                case_type="blocked_missing_required_fixture",
                notes="Required deterministic fixture is unavailable.",
            ),
        )
    )
    skipped = asyncio.run(
        run_answer_quality_case(
            FakeRuntime(),  # type: ignore[arg-type]
            AnswerQualityCase(
                case_id="optional_image",
                question="",
                case_type="skipped_optional_image",
                notes="Optional safe image is unavailable.",
            ),
        )
    )

    assert blocked.outcome == "BLOCKED"
    assert blocked.manual_review["blocked_reason"] == "blocked_missing_required_fixture"
    assert skipped.outcome == "SKIPPED"
    assert skipped.manual_review["skipped_reason"] == "skipped_optional_image"


def test_fixed_openrouter_requires_correct_service_evidence_and_n8n_needs_accepted_terms() -> None:
    cases = {case.case_id: case for case in fixed_answer_quality_cases()}
    openrouter = analyze_case_result(
        case=cases["openrouter_docs"],
        result=_pipeline_result(
            answer="Use the API key as documented.",
            accepted_text="OpenRouter API keys use the Authorization: Bearer header.",
        ),
        hydration={"doc-1": _doc("OpenRouter Docs", "external_docs", "active", "openrouter")},
        safety_state={},
    )
    n8n = analyze_case_result(
        case=cases["n8n_docs"],
        result=_pipeline_result(
            answer="No supported fragment was found.",
            accepted_text="",
            status=AnswerStatus.NEEDS_CLARIFICATION,
            answer_mode="out_of_base",
            include_accepted=False,
            selected_document_ids=("doc-1",),
        ),
        hydration={"doc-1": _doc("n8n Docs", "external_docs", "active", "n8n")},
        safety_state={},
    )

    assert openrouter.outcome == "PASS"
    assert n8n.outcome == "FAIL"
    assert not n8n.checks.accepted_evidence_present
    assert n8n.blocker_categories == ["evidence_selection_gap"]


def test_missing_required_group_with_correct_service_is_evidence_selection_gap() -> None:
    case = AnswerQualityCase(
        case_id="generic_service_case",
        question="q",
        case_type="official_docs",
        expected_service_ids=("example",),
        service_expectation="required",
        required_evidence_term_groups=(("required-anchor",),),
        requires_accepted_evidence=True,
    )
    analyzed = analyze_case_result(
        case=case,
        result=_pipeline_result(
            answer="Supported answer.",
            accepted_text="Different but service-correct evidence.",
        ),
        hydration={"doc-1": _doc("Example Docs", "external_docs", "active", "example")},
        safety_state={},
    )

    assert "required_evidence_group_missing" in analyzed.failure_codes
    assert analyzed.blocker_categories == ["evidence_selection_gap"]


def test_required_uploaded_source_class_missing_is_uploaded_material_routing_gap() -> None:
    case = AnswerQualityCase(
        case_id="generic_uploaded_case",
        question="q",
        case_type="uploaded_material_only",
        expected_source_types=("uploaded_or_local",),
        requires_accepted_evidence=True,
    )
    analyzed = analyze_case_result(
        case=case,
        result=_pipeline_result(
            answer="Supported answer.",
            accepted_text="External service evidence.",
        ),
        hydration={"doc-1": _doc("External Docs", "external_docs", "active", "example")},
        safety_state={},
    )

    assert "required_source_class_missing" in analyzed.failure_codes
    assert analyzed.blocker_categories == ["uploaded_material_routing_gap"]


def test_archived_evidence_has_separate_blocker_category() -> None:
    case = AnswerQualityCase(
        case_id="generic_archived_case",
        question="q",
        case_type="archived_exclusion",
    )
    analyzed = analyze_case_result(
        case=case,
        result=_pipeline_result(answer="Supported answer.", accepted_text="supported"),
        hydration={"doc-1": _doc("Archived", "external_docs", "archived", "example")},
        safety_state={},
    )

    assert "archived_evidence_present" in analyzed.failure_codes
    assert "archived_evidence_gap" in analyzed.blocker_categories


def test_followup_limitation_is_not_primary_phase7c_blocker() -> None:
    followup = AnswerQualityCaseResult(
        case_id="followup_without_memory",
        question="q",
        outcome="FAIL",
        failures=["expected limitation was not handled"],
        failure_codes=["known_deferred_limitation"],
        blocker_categories=["known_deferred_limitation"],
        blocking_for_current_phase=False,
    )

    overall, primary, next_phase = classify_baseline([followup])

    assert overall == "baseline_pass"
    assert primary == "no_blocking_functional_gap"
    assert "Phase 8A" in next_phase


def test_primary_blocker_uses_structured_categories_not_case_names() -> None:
    first = AnswerQualityCaseResult(
        case_id="arbitrary-one",
        question="q",
        outcome="FAIL",
        failure_codes=["required_evidence_missing"],
        blocker_categories=["evidence_selection_gap"],
    )
    second = AnswerQualityCaseResult(
        case_id="arbitrary-two",
        question="q",
        outcome="FAIL",
        failure_codes=["forbid_confident_service_violation"],
        blocker_categories=["ambiguous_service_routing_gap"],
    )

    overall, primary, _ = classify_baseline([first, second])
    single_overall, single_primary, _ = classify_baseline([first])

    assert overall == "functional_blocker_found"
    assert primary == "multiple_functional_blockers"
    assert single_overall == "functional_blocker_found"
    assert single_primary == "evidence_selection_gap"
    baseline = baseline_from_results(
        case_results=[first, second],
        git_sha="abc",
        workspace_id="workspace",
        safety_state={},
    )
    assert baseline.active_blocker_categories == [
        "ambiguous_service_routing_gap",
        "evidence_selection_gap",
    ]


def test_blocked_skipped_and_optional_warn_do_not_create_functional_blockers() -> None:
    cases = [
        AnswerQualityCaseResult(
            case_id="blocked-fixture",
            question="",
            outcome="BLOCKED",
            failure_codes=["fixture_missing"],
        ),
        AnswerQualityCaseResult(
            case_id="skipped-optional",
            question="",
            outcome="SKIPPED",
        ),
        AnswerQualityCaseResult(
            case_id="optional-warning",
            question="q",
            outcome="WARN",
            warnings=["optional evidence term groups were not satisfied"],
        ),
    ]

    overall, primary, _ = classify_baseline(cases)

    assert overall == "incomplete_environment"
    assert primary == "no_blocking_functional_gap"


def test_atomic_output_and_resume_behavior(tmp_path: Path) -> None:
    baseline = _valid_completed_resume_baseline()
    path = tmp_path / "baseline.json"

    save_baseline_atomic(path, baseline)
    loaded = load_existing_case_results(path)

    assert path.exists()
    assert not list(tmp_path.glob("*.tmp"))
    assert {case.case_id for case in loaded} == {case.case_id for case in baseline.case_results}


def test_resume_rejects_results_from_old_false_pass_schema(tmp_path: Path) -> None:
    path = tmp_path / "old-baseline.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "phase7c-a-answer-quality-baseline-v1",
                "case_results": [
                    {
                        "case_id": "ambiguous_service",
                        "question": "q",
                        "outcome": "PASS",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    try:
        load_existing_case_results(path)
    except ValueError as exc:
        assert "different harness schema" in str(exc)
    else:
        raise AssertionError("old false-pass results must not be resumed under the new contract")


def test_resume_rejects_schema_v2_before_any_case_reuse(tmp_path: Path) -> None:
    path = tmp_path / "v2-baseline.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "phase7c-answer-quality-baseline-v2",
                "case_results": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="different harness schema"):
        load_existing_case_results(path)


def test_runner_rejects_v2_resume_before_settings_or_production_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_answer_quality_baseline as runner

    path = tmp_path / "v2-runner-baseline.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "phase7c-answer-quality-baseline-v2",
                "case_results": [],
            }
        ),
        encoding="utf-8",
    )
    settings_called = False

    def fail_settings_loader() -> None:
        nonlocal settings_called
        settings_called = True
        raise AssertionError("settings loader must not run for an invalid resume artifact")

    monkeypatch.setattr(runner, "get_settings", fail_settings_loader)

    with pytest.raises(ValueError, match="different harness schema"):
        runner._run_confirmed(
            selected_ids=["telegram_docs"],
            output_path=path,
            resume=True,
            answer_mode="cheap",
        )
    assert settings_called is False


def test_resume_rejects_v3_without_complete_telemetry_contract(tmp_path: Path) -> None:
    path = tmp_path / "incomplete-v3-baseline.json"
    path.write_text(
        json.dumps(
                {
                    "schema_version": HARNESS_SCHEMA_VERSION,
                    "run_state": "completed",
                    "active_blocker_categories": [],
                    "production_safety_telemetry": {},
                    "case_results": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="incomplete production safety telemetry"):
        load_existing_case_results(path)


@pytest.mark.parametrize(
    ("field_name", "unsafe_value"),
    (
        ("supabase_write_attempts", 7),
        ("unknown_rpc_attempts", 1),
        ("telegram_message_attempts", 1),
        ("evidence_log_write_attempts", 1),
        ("blocked_write_attempts", 1),
        ("external_docs_operation_attempts", 1),
    ),
)
def test_resume_rejects_safety_pass_with_unsafe_attempt_counters(
    tmp_path: Path,
    field_name: str,
    unsafe_value: int,
) -> None:
    payload = _valid_completed_resume_payload()
    telemetry = payload["production_safety_telemetry"]
    assert isinstance(telemetry, dict)
    telemetry[field_name] = unsafe_value
    telemetry["safety_result"] = "PASS"

    _assert_resume_rejected(tmp_path, payload, "safety result")


@pytest.mark.parametrize(
    ("field_name", "unsafe_value"),
    (
        ("secret_values_rendered", True),
        ("read_only_adapter_enabled", False),
        ("evidence_logging_disabled", False),
        ("telegram_sending_disabled", False),
        ("external_docs_operations_disabled", False),
        ("manual_env_access_performed", True),
    ),
)
def test_resume_rejects_safety_pass_with_unsafe_runtime_flags(
    tmp_path: Path,
    field_name: str,
    unsafe_value: bool,
) -> None:
    payload = _valid_completed_resume_payload()
    telemetry = payload["production_safety_telemetry"]
    assert isinstance(telemetry, dict)
    telemetry[field_name] = unsafe_value
    telemetry["safety_result"] = "PASS"

    _assert_resume_rejected(tmp_path, payload, "safety result")


@pytest.mark.parametrize(
    ("field_name", "unsafe_value", "expected_error"),
    (
        ("select_calls", "0", "invalid counter"),
        ("model_attempts", -1, "invalid counter"),
        ("supabase_write_attempts", True, "invalid counter"),
    ),
)
def test_resume_rejects_malformed_operation_counters(
    tmp_path: Path,
    field_name: str,
    unsafe_value: object,
    expected_error: str,
) -> None:
    payload = _valid_completed_resume_payload()
    telemetry = payload["production_safety_telemetry"]
    assert isinstance(telemetry, dict)
    telemetry[field_name] = unsafe_value

    _assert_resume_rejected(tmp_path, payload, expected_error)


def test_resume_rejects_rpc_total_map_mismatch_and_unknown_rpc(tmp_path: Path) -> None:
    payload = _valid_completed_resume_payload()
    telemetry = payload["production_safety_telemetry"]
    assert isinstance(telemetry, dict)
    telemetry["allowlisted_rpc_calls_total"] = 2
    telemetry["allowlisted_rpc_calls_by_name"] = {"match_document_cards": 1}
    _assert_resume_rejected(tmp_path, payload, "RPC counters")

    payload = _valid_completed_resume_payload()
    telemetry = payload["production_safety_telemetry"]
    assert isinstance(telemetry, dict)
    telemetry["allowlisted_rpc_calls_total"] = 1
    telemetry["allowlisted_rpc_calls_by_name"] = {"dangerous_rpc": 1}
    _assert_resume_rejected(tmp_path, payload, "non-allowlisted RPC")


@pytest.mark.parametrize(
    ("table_name", "side", "replacement", "expected_error"),
    (
        ("documents", "before", {}, "malformed snapshot"),
        ("documents", "after", {}, "malformed snapshot"),
        ("documents", "before", {"snapshot_status": "complete", "safe_metadata_digest": "a" * 64}, "count"),
        ("documents", "before", {"snapshot_status": "complete", "row_count": 1}, "digest"),
        (
            "documents",
            "before",
            {"snapshot_status": "complete", "row_count": 1, "safe_metadata_digest": "not-a-digest"},
            "digest",
        ),
        (
            "documents",
            "before",
            {"snapshot_status": "incomplete", "row_count": None, "safe_metadata_digest": "", "error_code": "https://provider.example/secret"},
            "unsafe",
        ),
    ),
)
def test_resume_rejects_malformed_or_unsafe_snapshots(
    tmp_path: Path,
    table_name: str,
    side: str,
    replacement: dict[str, object],
    expected_error: str,
) -> None:
    payload = _valid_completed_resume_payload()
    telemetry = payload["production_safety_telemetry"]
    assert isinstance(telemetry, dict)
    snapshots = telemetry["table_snapshots"]
    assert isinstance(snapshots, dict)
    comparison = snapshots[table_name]
    assert isinstance(comparison, dict)
    comparison[side] = replacement

    _assert_resume_rejected(tmp_path, payload, expected_error)


def test_resume_rejects_snapshot_comparison_and_changed_flag_contradictions(tmp_path: Path) -> None:
    payload = _valid_completed_resume_payload()
    telemetry = payload["production_safety_telemetry"]
    assert isinstance(telemetry, dict)
    snapshots = telemetry["table_snapshots"]
    assert isinstance(snapshots, dict)
    documents = snapshots["documents"]
    assert isinstance(documents, dict)
    after = documents["after"]
    assert isinstance(after, dict)
    after["row_count"] = int(after["row_count"]) + 1
    telemetry["safety_result"] = "PASS"
    documents["comparison"] = "unchanged"
    telemetry["documents_changed"] = False
    _assert_resume_rejected(tmp_path, payload, "snapshot comparison")

    payload = _valid_completed_resume_payload()
    telemetry = payload["production_safety_telemetry"]
    assert isinstance(telemetry, dict)
    snapshots = telemetry["table_snapshots"]
    assert isinstance(snapshots, dict)
    chunks = snapshots["chunks"]
    assert isinstance(chunks, dict)
    after = chunks["after"]
    assert isinstance(after, dict)
    after["safe_metadata_digest"] = "b" * 64
    chunks["comparison"] = "changed"
    telemetry["chunks_changed"] = False
    telemetry["safety_result"] = "FAIL"
    _assert_resume_rejected(tmp_path, payload, "changed flag")


@pytest.mark.parametrize("saved_safety", ("PASS", "BLOCKED"))
def test_resume_rejects_saved_safety_result_mismatch(tmp_path: Path, saved_safety: str) -> None:
    payload = _valid_completed_resume_payload()
    telemetry = payload["production_safety_telemetry"]
    assert isinstance(telemetry, dict)
    telemetry["safety_result"] = saved_safety
    if saved_safety == "PASS":
        telemetry["secret_values_rendered"] = True
    else:
        telemetry["safety_result"] = "BLOCKED"

    _assert_resume_rejected(tmp_path, payload, "safety result")


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("counter_provenance", {}),
        ("counter_provenance", {"evidence_log_write_attempts": "disabled_by_construction"}),
        (
            "counter_provenance",
            {
                "evidence_log_write_attempts": "unknown",
                "telegram_message_attempts": "disabled_by_construction",
                "external_docs_operation_attempts": "disabled_by_construction",
            },
        ),
    ),
)
def test_resume_rejects_invalid_counter_provenance(
    tmp_path: Path,
    field_name: str,
    value: object,
) -> None:
    payload = _valid_completed_resume_payload()
    telemetry = payload["production_safety_telemetry"]
    assert isinstance(telemetry, dict)
    telemetry[field_name] = value

    _assert_resume_rejected(tmp_path, payload, "counter provenance")


def test_resume_rejects_case_verdict_codes_categories_and_blocking_mismatches(tmp_path: Path) -> None:
    for field_name, value, expected in (
        ("outcome", "FAIL", "verdict"),
        ("failure_codes", ["wrong_service_evidence"], "failure codes"),
        ("blocker_categories", ["explicit_service_routing_gap"], "blocker categories"),
        ("blocking_for_current_phase", False, "phase blocker"),
    ):
        payload = _valid_completed_resume_payload()
        case_row = next(row for row in payload["case_results"] if row["case_id"] == "telegram_docs")  # type: ignore[index]
        assert isinstance(case_row, dict)
        case_row[field_name] = value
        _assert_resume_rejected(tmp_path, payload, expected)


def test_resume_rejects_overall_classification_mismatches(tmp_path: Path) -> None:
    for field_name, value, expected in (
        ("overall_classification", "baseline_pass", "overall classification"),
        ("primary_blocker", "evidence_selection_gap", "primary blocker"),
        ("active_blocker_categories", ["evidence_selection_gap"], "active blocker categories"),
    ):
        payload = _valid_completed_resume_payload()
        payload[field_name] = value
        _assert_resume_rejected(tmp_path, payload, expected)


def test_resume_rejects_multiple_active_categories_with_non_multiple_primary(tmp_path: Path) -> None:
    payload = _valid_completed_resume_payload()
    case_results = payload["case_results"]
    assert isinstance(case_results, list)
    telegram = next(row for row in case_results if row["case_id"] == "telegram_docs")
    openrouter = next(row for row in case_results if row["case_id"] == "openrouter_docs")
    assert isinstance(telegram, dict)
    assert isinstance(openrouter, dict)

    telegram_checks = telegram["checks"]
    telegram_diagnostics = telegram["expectation_diagnostics"]
    assert isinstance(telegram_checks, dict)
    assert isinstance(telegram_diagnostics, dict)
    telegram_checks["required_evidence_groups_met"] = False
    telegram_checks["expected_terms_present"] = False
    telegram_diagnostics["required_evidence_term_groups"] = [
        {"alternatives": ["sendMessage"], "matched_terms": [], "satisfied": False}
    ]
    telegram["outcome"] = "FAIL"
    telegram["failure_codes"] = ["required_evidence_group_missing"]
    telegram["blocker_categories"] = ["evidence_selection_gap"]

    openrouter_checks = openrouter["checks"]
    openrouter_diagnostics = openrouter["expectation_diagnostics"]
    assert isinstance(openrouter_checks, dict)
    assert isinstance(openrouter_diagnostics, dict)
    openrouter_checks["service_expectation_met"] = False
    openrouter_diagnostics["selected_service_ids"] = ["supabase"]
    openrouter_diagnostics["accepted_evidence_service_ids"] = ["supabase"]
    openrouter_diagnostics["final_source_service_ids"] = ["supabase"]
    openrouter_diagnostics["unexpected_service_ids"] = ["supabase"]
    openrouter["outcome"] = "FAIL"
    openrouter["failure_codes"] = ["wrong_service_evidence"]
    openrouter["blocker_categories"] = ["explicit_service_routing_gap"]

    payload["overall_classification"] = "functional_blocker_found"
    payload["active_blocker_categories"] = ["evidence_selection_gap", "explicit_service_routing_gap"]
    payload["primary_blocker"] = "evidence_selection_gap"
    payload["recommended_next_phase"] = "Phase 7C-B - one focused fix for multiple_functional_blockers"

    _assert_resume_rejected(tmp_path, payload, "primary blocker")


def test_resume_rejects_deferred_followup_saved_as_active_blocker(tmp_path: Path) -> None:
    payload = _valid_completed_resume_payload()
    followup = next(row for row in payload["case_results"] if row["case_id"] == "followup_without_memory")  # type: ignore[index]
    assert isinstance(followup, dict)
    checks = followup["checks"]
    assert isinstance(checks, dict)
    checks["answer_non_empty"] = False
    followup["outcome"] = "FAIL"
    followup["failure_codes"] = ["known_deferred_limitation"]
    followup["blocker_categories"] = ["known_deferred_limitation"]
    followup["blocking_for_current_phase"] = True

    _assert_resume_rejected(tmp_path, payload, "phase blocker")


def test_resume_rejects_unknown_duplicate_missing_and_in_progress_cases(tmp_path: Path) -> None:
    payload = _valid_completed_resume_payload()
    payload["case_results"] = list(payload["case_results"]) + [dict(payload["case_results"][0])]  # type: ignore[index]
    _assert_resume_rejected(tmp_path, payload, "duplicate case id")

    payload = _valid_completed_resume_payload()
    payload["case_results"] = list(payload["case_results"])[:-1]  # type: ignore[index]
    _assert_resume_rejected(tmp_path, payload, "missing required case")

    payload = _valid_completed_resume_payload()
    unknown = dict(payload["case_results"][0])  # type: ignore[index]
    unknown["case_id"] = "unknown_case"
    payload["case_results"] = list(payload["case_results"]) + [unknown]  # type: ignore[index]
    _assert_resume_rejected(tmp_path, payload, "unknown case id")

    payload = _valid_completed_resume_payload()
    payload["run_state"] = "in_progress"
    _assert_resume_rejected(tmp_path, payload, "run_state is completed")


def test_runner_rejects_contradictory_v3_resume_before_settings_or_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_answer_quality_baseline as runner

    payload = _valid_completed_resume_payload()
    telemetry = payload["production_safety_telemetry"]
    assert isinstance(telemetry, dict)
    telemetry["supabase_write_attempts"] = 7
    telemetry["safety_result"] = "PASS"
    path = _write_resume_payload(tmp_path, payload)
    settings_called = False
    runtime_called = False

    def fail_settings_loader() -> None:
        nonlocal settings_called
        settings_called = True
        raise AssertionError("settings loader must not run for invalid resume")

    def fail_runtime_builder(*args: object, **kwargs: object) -> None:
        nonlocal runtime_called
        runtime_called = True
        raise AssertionError("runtime must not be built for invalid resume")

    monkeypatch.setattr(runner, "get_settings", fail_settings_loader)
    monkeypatch.setattr(runner, "build_answer_quality_runtime_from_settings", fail_runtime_builder)

    with pytest.raises(ValueError, match="safety result"):
        runner._run_confirmed(
            selected_ids=["telegram_docs"],
            output_path=path,
            resume=True,
            answer_mode="cheap",
        )
    assert settings_called is False
    assert runtime_called is False


def test_runner_treats_valid_completed_v3_resume_as_already_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_answer_quality_baseline as runner

    path = _write_resume_payload(tmp_path, _valid_completed_resume_payload())
    settings_called = False

    def fail_settings_loader() -> None:
        nonlocal settings_called
        settings_called = True
        raise AssertionError("settings loader must not run for completed resume")

    monkeypatch.setattr(runner, "get_settings", fail_settings_loader)

    result = runner._run_confirmed(
        selected_ids=["telegram_docs"],
        output_path=path,
        resume=True,
        answer_mode="cheap",
    )

    assert result == 0
    assert settings_called is False


def test_secret_like_values_are_removed_from_report_dict() -> None:
    fake_bearer = "Bearer " + "abcdefghijklmnopqrstuvwxyz"
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
                final_answer=fake_bearer,
                expectation_diagnostics={
                    "accepted_evidence_service_ids": ["openrouter"],
                    "diagnostic_error": fake_bearer,
                },
            )
        ],
        overall_classification="baseline_pass",
        primary_blocker="no_blocking_functional_gap",
        recommended_next_phase="Phase 8A",
        active_blocker_categories=[],
        production_safety_telemetry=_passing_telemetry(),
    )

    payload = json.dumps(dataclass_to_sanitized_dict(baseline), ensure_ascii=False)

    assert fake_bearer not in payload
    assert "<secret-redacted>" in payload
    assert "accepted_evidence_service_ids" in payload


def test_product_fail_does_not_require_harness_process_failure() -> None:
    case_result = AnswerQualityCaseResult(
        case_id="telegram_docs",
        question="q",
        outcome="FAIL",
        failures=["expected source class was not selected"],
        failure_codes=["wrong_service_evidence"],
        blocker_categories=["explicit_service_routing_gap"],
    )

    baseline = baseline_from_results(
        case_results=[case_result],
        git_sha="abc",
        workspace_id="workspace",
        safety_state={"write_attempts": 0, "blocked_write_calls": 0, "non_allowlisted_rpc_attempts": 0},
    )

    assert baseline.overall_classification == "functional_blocker_found"
    assert baseline.primary_blocker == "explicit_service_routing_gap"
    assert baseline.active_blocker_categories == ["explicit_service_routing_gap"]
    assert baseline.supabase_write_attempts == 0


def _valid_completed_resume_baseline() -> AnswerQualityBaseline:
    cases = fixed_answer_quality_cases()
    results: list[AnswerQualityCaseResult] = []
    evidence_by_case = {
        "telegram_docs": "Use sendMessage with chat_id and text.",
        "n8n_docs": "Configure the HTTP Request node with method and headers.",
        "openrouter_docs": "Use an API key in the Authorization Bearer header.",
        "supabase_docs": "Use pgvector embeddings with match_documents for vector search.",
    }
    for case in cases:
        if case.case_id in evidence_by_case:
            service_id = case.expected_service_ids[0]
            source_class = "external_docs"
            hydration = {
                "doc-1": DocumentSummary(
                    document_id_hash="doc1",
                    title=f"{service_id} docs",
                    source_type="external_docs",
                    source_class=source_class,
                    status="active",
                    service_ids=(service_id,),
                )
            }
            results.append(
                analyze_case_result(
                    case=case,
                    result=_pipeline_result(
                        answer="Grounded answer.",
                        accepted_text=evidence_by_case[case.case_id],
                    ),
                    hydration=hydration,
                    safety_state={},
                )
            )
        else:
            results.append(
                analyze_case_result(
                    case=case,
                    result=_pipeline_result(
                        answer="Нужно уточнить вопрос.",
                        accepted_text="",
                        status=AnswerStatus.NEEDS_CLARIFICATION,
                        answer_mode="ask_for_missing_data",
                        include_accepted=False,
                        selected_document_ids=(),
                    ),
                    hydration={},
                    safety_state={},
                )
            )
    results.extend(
        [
            AnswerQualityCaseResult(
                case_id="uploaded_material_only_auto",
                question="",
                outcome="BLOCKED",
                warnings=["No active uploaded fixture."],
                failure_codes=["fixture_missing"],
                blocker_categories=[],
                blocking_for_current_phase=True,
            ),
            AnswerQualityCaseResult(
                case_id="mixed_course_service_auto",
                question="",
                outcome="BLOCKED",
                warnings=["No mixed source fixture."],
                failure_codes=["fixture_missing"],
                blocker_categories=[],
                blocking_for_current_phase=True,
            ),
            AnswerQualityCaseResult(
                case_id="archived_exclusion",
                question="",
                outcome="BLOCKED",
                warnings=["No archived fixture."],
                failure_codes=["fixture_missing"],
                blocker_categories=[],
                blocking_for_current_phase=True,
            ),
            AnswerQualityCaseResult(
                case_id="vision_optional",
                question="",
                outcome="SKIPPED",
                warnings=["No safe image fixture."],
                failure_codes=[],
                blocker_categories=[],
                blocking_for_current_phase=False,
            ),
        ]
    )
    return baseline_from_results(
        case_results=results,
        git_sha="abc",
        workspace_id="workspace",
        safety_state={
            "allowed_selects": 0,
            "allowed_rpc_calls": 0,
            "allowed_rpc_calls_by_name": {},
            "write_attempts": 0,
            "blocked_write_calls": 0,
            "non_allowlisted_rpc_attempts": 0,
        },
        operation_state=HarnessOperationState(settings_loader_attempted=True, settings_loader_used=True),
        before_snapshots=_snapshot_set(),
        after_snapshots=_snapshot_set(),
        run_state="completed",
    )


def _valid_completed_resume_payload() -> dict[str, object]:
    return dataclass_to_sanitized_dict(_valid_completed_resume_baseline())  # type: ignore[return-value]


def _write_resume_payload(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "resume-artifact.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _assert_resume_rejected(tmp_path: Path, payload: dict[str, object], match: str = "Cannot resume") -> None:
    with pytest.raises(ValueError, match=match):
        load_existing_case_results(_write_resume_payload(tmp_path, payload))


def _snapshot_set(
    *,
    row_count: int = 1,
    digest: str = "a" * 64,
) -> dict[str, TableStateSnapshot]:
    return {
        name: TableStateSnapshot(
            row_count=row_count,
            safe_metadata_digest=digest,
            snapshot_status="complete",
        )
        for name in (
            "documents",
            "document_versions",
            "sections",
            "chunks",
            "conversations",
            "messages",
            "term_statistics",
        )
    }


def _passing_telemetry() -> ProductionSafetyTelemetry:
    snapshots = _snapshot_set()
    return build_production_safety_telemetry(
        safety_state={
            "allowed_selects": 0,
            "allowed_rpc_calls": 0,
            "allowed_rpc_calls_by_name": {},
            "write_attempts": 0,
            "blocked_write_calls": 0,
            "non_allowlisted_rpc_attempts": 0,
        },
        operation_state=HarnessOperationState(settings_loader_attempted=True, settings_loader_used=True),
        before_snapshots=snapshots,
        after_snapshots=snapshots,
    )


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
    answer_mode: str = "answer_from_materials",
    include_accepted: bool = True,
    selected_document_ids: tuple[str, ...] = ("doc-1",),
) -> PipelineResult:
    sources: list[SourceRef] = []
    selected_documents = [{"document_id": document_id} for document_id in selected_document_ids]
    accepted_evidence: list[dict[str, object]] = []
    if include_accepted:
        sources.append(SourceRef(document_id="doc-1", document_title="Doc", evidence_id="ev-1"))
        accepted_evidence.append(
            {
                "evidence_id": "ev-1",
                "document_id": "doc-1",
                "document_title": "Doc",
                "text": accepted_text,
                "score": 0.9,
            }
        )
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
            "answer_mode": answer_mode,
        },
    )


def _analyze_required_term(term: str, evidence: str) -> AnswerQualityCaseResult:
    return analyze_case_result(
        case=AnswerQualityCase(
            case_id="boundary_contract",
            question="q",
            case_type="official_docs",
            required_evidence_term_groups=((term,),),
            requires_accepted_evidence=True,
        ),
        result=_pipeline_result(answer="Supported answer.", accepted_text=evidence),
        hydration={"doc-1": _doc("Docs", "external_docs", "active", "")},
        safety_state={},
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
        service_ids=(source_name,) if source_name else (),
    )
