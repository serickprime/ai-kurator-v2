"""Safe answer-quality harness for no-write RAG baseline audits."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
import time
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from app.db.supabase_client import SupabaseClient, SupabaseRequestError
from app.llm.embeddings import OllamaEmbeddingClient
from app.llm.model_router import ModelRoutedAnswerClient, ModelRouter, ModelRouterConfig
from app.llm.openrouter_client import OpenRouterClient
from app.rag.answer_generator import AnswerGenerator
from app.rag.claim_verifier import ClaimVerifier
from app.rag.document_router import DocumentRouter, SupabaseDocumentCardStore
from app.rag.evidence_pack import EvidencePackBuilder
from app.rag.evidence_retriever import EvidenceRetriever, SupabaseEvidenceChunkStore
from app.rag.pipeline import EvidenceFirstRagPipeline
from app.rag.question_analysis import QuestionAnalyzer
from app.rag.reranker import EvidenceReranker
from app.rag.source_labels import SourceLabelBuilder

if TYPE_CHECKING:
    from app.config import Settings
    from app.rag.types import PipelineResult


HARNESS_SCHEMA_VERSION = "phase7c-answer-quality-baseline-v3"
READ_ONLY_RPC_ALLOWLIST = frozenset(
    {
        "match_document_cards",
        "hybrid_match_chunks_in_documents",
        "match_chunks_in_documents",
    }
)
SOURCE_TYPE_EXTERNAL = "external_docs"
SOURCE_CLASS_EXTERNAL = "external_docs"
SOURCE_CLASS_UPLOADED = "uploaded_or_local"
SOURCE_CLASS_UNKNOWN = "unknown"
RESIDUE_SIGNALS = (
    "dev_page_image",
    "srcset=",
    "class-heavy image wrappers",
    "/file/",
    "<picture",
    "<img",
    "footer",
    "navigation",
)
SECRET_PATTERNS = (
    re.compile(r"\bsk-or-[A-Za-z0-9_-]{20,}\b", re.IGNORECASE),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b(?:bot)?[0-9]{6,}(?::|%3[Aa])[A-Za-z0-9_-]{20,}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),
)
UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.I)

CaseOutcome = Literal["PASS", "WARN", "FAIL", "BLOCKED", "SKIPPED"]
ServiceExpectation = Literal["none", "required", "forbid_confident"]
SafetyResult = Literal["PASS", "FAIL", "BLOCKED"]
RunState = Literal["in_progress", "completed", "failed"]
SnapshotStatus = Literal["complete", "incomplete"]
SnapshotComparison = Literal["unchanged", "changed", "incomplete"]
ChangedState = bool | Literal["unknown"]
CounterProvenanceMode = Literal["instrumented", "disabled_by_construction"]
FailureCode = Literal[
    "final_answer_missing",
    "status_expectation_mismatch",
    "answer_mode_mismatch",
    "inactive_selected_document",
    "inactive_accepted_evidence",
    "required_evidence_missing",
    "source_evidence_mismatch",
    "archived_evidence_present",
    "internal_metadata_leak",
    "secret_like_output",
    "documentation_residue_present",
    "required_source_class_missing",
    "required_service_missing",
    "wrong_service_evidence",
    "forbid_confident_service_violation",
    "required_evidence_group_missing",
    "answered_without_evidence",
    "fixture_missing",
    "known_deferred_limitation",
]
BlockerCategory = Literal[
    "course_material_routing_gap",
    "course_alias_wiring_gap",
    "explicit_service_routing_gap",
    "ambiguous_service_routing_gap",
    "uploaded_material_routing_gap",
    "mixed_source_allocation_gap",
    "evidence_selection_gap",
    "archived_evidence_gap",
    "grounding_gap",
    "answer_status_gap",
    "answer_generation_gap",
    "citation_source_label_gap",
    "documentation_residue_affects_answers",
    "known_deferred_limitation",
    "unclassified_functional_gap",
]
OverallClassification = Literal[
    "baseline_pass",
    "functional_blocker_found",
    "incomplete_environment",
    "harness_failure",
]
PrimaryBlocker = Literal[
    "course_material_routing_gap",
    "course_alias_wiring_gap",
    "explicit_service_routing_gap",
    "ambiguous_service_routing_gap",
    "uploaded_material_routing_gap",
    "mixed_source_allocation_gap",
    "evidence_selection_gap",
    "archived_evidence_gap",
    "grounding_gap",
    "answer_status_gap",
    "answer_generation_gap",
    "citation_source_label_gap",
    "insufficient_evidence_handling_gap",
    "documentation_residue_affects_answers",
    "multiple_functional_blockers",
    "unclassified_functional_gap",
    "no_blocking_functional_gap",
]

SAFETY_SNAPSHOT_FIELDS: dict[str, tuple[str, ...]] = {
    "documents": (
        "id",
        "workspace_id",
        "source_type",
        "filename",
        "document_key",
        "title",
        "course",
        "module",
        "lesson",
        "version",
        "status",
        "content_hash",
        "metadata",
        "created_at",
        "updated_at",
    ),
    "sections": (
        "id",
        "document_id",
        "workspace_id",
        "section_index",
        "heading",
        "summary",
        "page_start",
        "page_end",
        "metadata",
        "section_embedding",
    ),
    "chunks": (
        "id",
        "document_id",
        "section_id",
        "workspace_id",
        "chunk_index",
        "content",
        "embedding",
        "token_count",
        "page",
        "heading",
        "metadata",
        "created_at",
    ),
    "conversations": (
        "id",
        "telegram_user_id",
        "workspace_id",
        "title",
        "summary",
        "is_active",
        "created_at",
        "updated_at",
    ),
    "messages": (
        "id",
        "conversation_id",
        "telegram_user_id",
        "role",
        "content",
        "attachments",
        "metadata",
        "created_at",
    ),
    "term_statistics": (
        "id",
        "workspace_id",
        "term",
        "normalized_term",
        "document_frequency",
        "chunk_frequency",
        "course_frequency",
        "first_seen_at",
        "last_seen_at",
        "examples",
        "term_type_guess",
        "metadata",
        "created_at",
        "updated_at",
    ),
}
DOCUMENT_VERSION_FIELDS = (
    "id",
    "workspace_id",
    "document_key",
    "version",
    "status",
    "content_hash",
    "created_at",
    "updated_at",
)
SAFETY_SNAPSHOT_NAMES = (
    "documents",
    "document_versions",
    "sections",
    "chunks",
    "conversations",
    "messages",
    "term_statistics",
)
ANSWER_QUALITY_DYNAMIC_CASE_IDS = (
    "uploaded_material_only_auto",
    "mixed_course_service_auto",
    "archived_exclusion",
    "vision_optional",
)
OPERATION_COUNTER_FIELDS = (
    "select_calls",
    "allowlisted_rpc_calls_total",
    "unknown_rpc_attempts",
    "supabase_write_attempts",
    "blocked_write_attempts",
    "evidence_log_write_attempts",
    "telegram_message_attempts",
    "external_docs_operation_attempts",
    "model_attempts",
)
RUNTIME_FLAG_FIELDS = (
    "evidence_logging_disabled",
    "telegram_sending_disabled",
    "external_docs_operations_disabled",
    "read_only_adapter_enabled",
    "atomic_output_enabled",
    "manual_env_access_performed",
    "secret_values_rendered",
    "settings_loader_attempted",
    "settings_loader_used",
)
COUNTER_PROVENANCE_FIELDS = (
    "evidence_log_write_attempts",
    "telegram_message_attempts",
    "external_docs_operation_attempts",
)
VALID_COUNTER_PROVENANCE_MODES = {"instrumented", "disabled_by_construction"}
SNAPSHOT_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ERROR_CODE_RE = re.compile(r"^[a-z0-9_:-]{1,120}$")


class ReadOnlyViolation(RuntimeError):
    """Raised when the audit attempts a write-capable Supabase operation."""


@dataclass
class ReadOnlySafetyState:
    """Counters for the read-only Supabase boundary."""

    allowed_selects: int = 0
    allowed_rpc_calls: int = 0
    write_attempts: int = 0
    blocked_write_calls: int = 0
    non_allowlisted_rpc_attempts: int = 0
    blocked_operations: list[str] = field(default_factory=list)
    allowed_rpc_names: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, object]:
        """Return a JSON-safe safety snapshot."""
        rpc_counts = dict(sorted(Counter(self.allowed_rpc_names).items()))
        return {
            "allowed_selects": self.allowed_selects,
            "allowed_rpc_calls": self.allowed_rpc_calls,
            "allowed_rpc_names": sorted(set(self.allowed_rpc_names)),
            "allowed_rpc_calls_by_name": rpc_counts,
            "rpc_allowlist": sorted(READ_ONLY_RPC_ALLOWLIST),
            "write_attempts": self.write_attempts,
            "blocked_write_calls": self.blocked_write_calls,
            "non_allowlisted_rpc_attempts": self.non_allowlisted_rpc_attempts,
            "blocked_operations": list(self.blocked_operations),
        }


@dataclass
class HarnessOperationState:
    """Non-Supabase operation counters and runtime safety flags."""

    evidence_log_write_attempts: int = 0
    telegram_message_attempts: int = 0
    external_docs_operation_attempts: int = 0
    model_attempts: int = 0
    evidence_logging_disabled: bool = True
    telegram_sending_disabled: bool = True
    read_only_adapter_enabled: bool = True
    atomic_output_enabled: bool = True
    manual_env_access_performed: bool = False
    secret_values_rendered: bool = False
    settings_loader_attempted: bool = False
    settings_loader_used: bool = False
    external_docs_operations_disabled: bool = True


@dataclass(frozen=True)
class TableStateSnapshot:
    """Sanitized state fingerprint for one production table or logical view."""

    row_count: int | None
    safe_metadata_digest: str
    snapshot_status: SnapshotStatus
    error_code: str = ""
    source_relation: str = ""


@dataclass(frozen=True)
class TableSafetyComparison:
    """Before/after comparison for one production table or logical view."""

    before: TableStateSnapshot
    after: TableStateSnapshot
    comparison: SnapshotComparison


@dataclass
class ProductionSafetyTelemetry:
    """Artifact-level proof of the harness production safety boundary."""

    safety_result: SafetyResult
    select_calls: int
    allowlisted_rpc_calls_total: int
    allowlisted_rpc_calls_by_name: dict[str, int]
    unknown_rpc_attempts: int
    supabase_write_attempts: int
    blocked_write_attempts: int
    evidence_log_write_attempts: int
    telegram_message_attempts: int
    external_docs_operation_attempts: int
    model_attempts: int
    evidence_logging_disabled: bool
    telegram_sending_disabled: bool
    read_only_adapter_enabled: bool
    atomic_output_enabled: bool
    manual_env_access_performed: bool
    secret_values_rendered: bool
    settings_loader_attempted: bool
    settings_loader_used: bool
    external_docs_operations_disabled: bool
    counter_provenance: dict[str, CounterProvenanceMode]
    table_snapshots: dict[str, TableSafetyComparison]
    documents_changed: ChangedState
    document_versions_changed: ChangedState
    sections_changed: ChangedState
    chunks_changed: ChangedState
    conversations_changed: ChangedState
    messages_changed: ChangedState
    term_statistics_changed: ChangedState


class ReadOnlySupabaseClient:
    """Narrow Supabase adapter exposing only reads needed by the harness."""

    def __init__(self, client: Any, *, rpc_allowlist: set[str] | frozenset[str] = READ_ONLY_RPC_ALLOWLIST) -> None:
        self._client = client
        self._rpc_allowlist = frozenset(rpc_allowlist)
        self.safety = ReadOnlySafetyState()

    async def select(self, table: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Delegate PostgREST select operations."""
        self.safety.allowed_selects += 1
        return await self._client.select(table, params=params)

    async def rpc(self, function_name: str, payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Delegate only allowlisted stable read-only RPC calls."""
        if function_name not in self._rpc_allowlist:
            self.safety.non_allowlisted_rpc_attempts += 1
            self._block(f"rpc:{function_name}")
        self.safety.allowed_rpc_calls += 1
        self.safety.allowed_rpc_names.append(function_name)
        return await self._client.rpc(function_name, payload or {})

    async def close(self) -> None:
        """Close the wrapped client."""
        await self._client.close()

    async def insert(self, table: str, payload: object) -> list[dict[str, Any]]:
        """Block inserts."""
        del table, payload
        self._block("insert")

    async def update(self, table: str, payload: object, params: object) -> list[dict[str, Any]]:
        """Block updates."""
        del table, payload, params
        self._block("update")

    async def delete(self, table: str, params: object) -> list[dict[str, Any]]:
        """Block deletes."""
        del table, params
        self._block("delete")

    async def upsert(self, table: str, payload: object) -> list[dict[str, Any]]:
        """Block upserts."""
        del table, payload
        self._block("upsert")

    async def post(self, *args: object, **kwargs: object) -> object:
        """Block generic POST-style calls."""
        del args, kwargs
        self._block("post")

    async def patch(self, *args: object, **kwargs: object) -> object:
        """Block generic PATCH-style calls."""
        del args, kwargs
        self._block("patch")

    async def put(self, *args: object, **kwargs: object) -> object:
        """Block generic PUT-style calls."""
        del args, kwargs
        self._block("put")

    async def request(self, *args: object, **kwargs: object) -> object:
        """Block generic request calls."""
        del args, kwargs
        self._block("request")

    def _block(self, operation: str) -> None:
        self.safety.write_attempts += 1
        self.safety.blocked_write_calls += 1
        self.safety.blocked_operations.append(operation)
        raise ReadOnlyViolation(f"Read-only answer-quality harness blocked Supabase operation: {operation}")


@dataclass(frozen=True)
class AnswerQualityCase:
    """One answer-quality baseline case."""

    case_id: str
    question: str
    case_type: str
    expected_service_ids: tuple[str, ...] = ()
    service_expectation: ServiceExpectation = "none"
    expected_source_types: tuple[str, ...] = ()
    expected_terms: tuple[str, ...] = ()
    required_evidence_term_groups: tuple[tuple[str, ...], ...] = ()
    optional_evidence_term_groups: tuple[tuple[str, ...], ...] = ()
    allowed_statuses: tuple[str, ...] = ("answered", "insufficient_evidence", "needs_clarification")
    allowed_answer_modes: tuple[str, ...] = ()
    requires_accepted_evidence: bool = False
    requires_mixed_sources: bool = False
    check_archived_exclusion: bool = True
    blocking_for_current_phase: bool = True
    expected_limitation: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if self.service_expectation == "required" and not self.expected_service_ids:
            raise ValueError("required service expectation needs at least one expected service id")
        for group in (*self.required_evidence_term_groups, *self.optional_evidence_term_groups):
            if not group or not all(str(term).strip() for term in group):
                raise ValueError("evidence term groups must contain non-empty alternatives")


@dataclass
class DocumentSummary:
    """Sanitized document metadata used by the audit."""

    document_id_hash: str
    title: str
    source_type: str
    source_class: str
    status: str
    version: int | None = None
    course: str = ""
    source_name: str = ""
    service_ids: tuple[str, ...] = ()


@dataclass
class CaseChecks:
    """Automated checks for one case."""

    answer_non_empty: bool = False
    status_allowed: bool = False
    answer_mode_allowed: bool = False
    selected_documents_active: bool = False
    accepted_evidence_present: bool = False
    accepted_evidence_active: bool = False
    sources_from_accepted_evidence: bool = False
    archived_absent: bool = False
    required_source_types_present: bool = False
    service_expectation_met: bool = False
    required_evidence_groups_met: bool = False
    optional_evidence_groups_met: bool = False
    no_raw_uuid_in_answer: bool = False
    no_debug_metadata_in_answer: bool = False
    no_evidence_block_leak: bool = False
    no_secret_like_text: bool = False
    no_residue_in_answer: bool = False
    expected_terms_present: bool = False
    insufficient_evidence_handled: bool = False


@dataclass
class AnswerQualityCaseResult:
    """Sanitized result for one baseline case."""

    case_id: str
    question: str
    outcome: CaseOutcome
    pipeline_status: str = ""
    final_answer: str = ""
    selected_documents: list[DocumentSummary] = field(default_factory=list)
    selected_source_classes: list[str] = field(default_factory=list)
    selected_services: list[str] = field(default_factory=list)
    detected_service_metadata: dict[str, object] = field(default_factory=dict)
    accepted_evidence_summary: list[dict[str, object]] = field(default_factory=list)
    final_sources: list[str] = field(default_factory=list)
    checks: CaseChecks = field(default_factory=CaseChecks)
    expectation_diagnostics: dict[str, object] = field(default_factory=dict)
    manual_review: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    failure_codes: list[FailureCode] = field(default_factory=list)
    blocker_categories: list[BlockerCategory] = field(default_factory=list)
    blocking_for_current_phase: bool = True
    duration_seconds: float = 0.0
    model_metadata: dict[str, object] = field(default_factory=dict)
    read_only_safety_state: dict[str, object] = field(default_factory=dict)


@dataclass
class AnswerQualityBaseline:
    """Top-level sanitized baseline artifact."""

    schema_version: str
    generated_at: str
    git_sha: str
    workspace: str
    evidence_logging_disabled: bool
    telegram_sending_disabled: bool
    supabase_write_attempts: int
    blocked_write_calls: int
    non_allowlisted_rpc_attempts: int
    case_results: list[AnswerQualityCaseResult]
    overall_classification: OverallClassification
    primary_blocker: PrimaryBlocker
    recommended_next_phase: str
    active_blocker_categories: list[BlockerCategory]
    production_safety_telemetry: ProductionSafetyTelemetry
    read_only_safety_state: dict[str, object] = field(default_factory=dict)
    run_state: RunState = "completed"


@dataclass
class AnswerQualityRuntimeResources:
    """Long-lived resources owned by the answer-quality runtime."""

    supabase: ReadOnlySupabaseClient
    openrouter_client: OpenRouterClient
    embedding_client: OllamaEmbeddingClient
    operation_state: HarnessOperationState

    async def close(self) -> None:
        """Close all owned HTTP clients."""
        await self.supabase.close()
        await self.openrouter_client.close()
        await self.embedding_client.close()


@dataclass
class AnswerQualityRuntime:
    """Separate no-write runtime for answer-quality audits."""

    pipeline: EvidenceFirstRagPipeline
    resources: AnswerQualityRuntimeResources
    workspace_id: str
    answer_mode: str

    async def close(self) -> None:
        """Close owned runtime resources."""
        await self.resources.close()


def fixed_answer_quality_cases() -> list[AnswerQualityCase]:
    """Return fixed Phase 7C-A baseline cases."""
    return [
        AnswerQualityCase(
            case_id="telegram_docs",
            question="как отправить сообщение через Telegram Bot API?",
            case_type="official_docs",
            expected_service_ids=("telegram_bot_api",),
            service_expectation="required",
            expected_source_types=(SOURCE_CLASS_EXTERNAL,),
            required_evidence_term_groups=(("sendMessage",), ("chat_id",), ("text",)),
            requires_accepted_evidence=True,
        ),
        AnswerQualityCase(
            case_id="n8n_docs",
            question="как отправить HTTP-запрос из n8n?",
            case_type="official_or_course_docs",
            expected_service_ids=("n8n",),
            service_expectation="required",
            required_evidence_term_groups=(
                ("HTTP Request", "HTTP Request node"),
                ("method", "headers", "body"),
            ),
            requires_accepted_evidence=True,
        ),
        AnswerQualityCase(
            case_id="openrouter_docs",
            question="как подключить OpenRouter API ключ?",
            case_type="official_docs",
            expected_service_ids=("openrouter",),
            service_expectation="required",
            expected_source_types=(SOURCE_CLASS_EXTERNAL,),
            required_evidence_term_groups=(
                ("API key", "API keys"),
                ("Authorization", "Bearer"),
            ),
            requires_accepted_evidence=True,
        ),
        AnswerQualityCase(
            case_id="supabase_docs",
            question="как сделать векторный поиск по документам в Supabase?",
            case_type="official_docs",
            expected_service_ids=("supabase",),
            service_expectation="required",
            required_evidence_term_groups=(
                ("pgvector",),
                ("embeddings", "match_documents", "vector search"),
            ),
            requires_accepted_evidence=True,
        ),
        AnswerQualityCase(
            case_id="ambiguous_service",
            question="как подключить API и настроить запрос?",
            case_type="ambiguous_service",
            service_expectation="forbid_confident",
            allowed_statuses=("insufficient_evidence", "needs_clarification"),
            allowed_answer_modes=("ask_for_missing_data", "out_of_base"),
            expected_limitation="service_ambiguous",
        ),
        AnswerQualityCase(
            case_id="out_of_base",
            question="какой у меня сейчас баланс на банковской карте?",
            case_type="out_of_base",
            allowed_statuses=("insufficient_evidence", "needs_clarification"),
            allowed_answer_modes=("ask_for_missing_data", "out_of_base"),
            expected_limitation="out_of_base",
        ),
        AnswerQualityCase(
            case_id="followup_without_memory",
            question="а какой параметр нужно указать для этого?",
            case_type="followup_without_memory",
            allowed_statuses=("insufficient_evidence", "needs_clarification"),
            allowed_answer_modes=("ask_for_missing_data", "out_of_base"),
            blocking_for_current_phase=False,
            expected_limitation="conversation_memory_not_implemented",
        ),
    ]


class _TelemetryModelClient:
    """Count actual provider attempts without exposing model request data."""

    def __init__(self, client: OpenRouterClient, operation_state: HarnessOperationState) -> None:
        self._client = client
        self._operation_state = operation_state

    async def complete_text_with_model(self, model: str, messages: list[dict[str, str]]) -> str:
        self._operation_state.model_attempts += 1
        return await self._client.complete_text_with_model(model, messages)

    async def complete_vision_with_model(self, model: str, image_payload: object, prompt: str) -> str:
        self._operation_state.model_attempts += 1
        return await self._client.complete_vision_with_model(model, image_payload, prompt)


def build_answer_quality_runtime_from_settings(
    settings: "Settings",
    *,
    answer_mode: str = "cheap",
    operation_state: HarnessOperationState | None = None,
) -> AnswerQualityRuntime:
    """Build a separate no-write RAG runtime without Telegram dependencies."""
    if not settings.default_workspace_id:
        raise RuntimeError("DEFAULT_WORKSPACE_ID is required for the answer-quality harness")

    operation_state = operation_state or HarnessOperationState()
    supabase = ReadOnlySupabaseClient(SupabaseClient(settings))
    openrouter_client = OpenRouterClient(settings)
    embedding_client = OllamaEmbeddingClient(settings)
    model_router = ModelRouter(
        _TelemetryModelClient(openrouter_client, operation_state),
        ModelRouterConfig.from_settings(settings),
    )
    answer_client = ModelRoutedAnswerClient(model_router, default_answer_mode=answer_mode)
    pipeline = EvidenceFirstRagPipeline(
        analyzer=QuestionAnalyzer(),
        router=DocumentRouter(
            store=SupabaseDocumentCardStore(supabase),
            embedding_client=embedding_client,
        ),
        retriever=EvidenceRetriever(
            chunk_store=SupabaseEvidenceChunkStore(supabase),
            embedding_client=embedding_client,
            workspace_id=settings.default_workspace_id,
        ),
        reranker=EvidenceReranker(),
        pack_builder=EvidencePackBuilder(),
        answer_generator=AnswerGenerator(answer_client),
        verifier=ClaimVerifier(),
        logger=None,
    )
    return AnswerQualityRuntime(
        pipeline=pipeline,
        resources=AnswerQualityRuntimeResources(
            supabase=supabase,
            openrouter_client=openrouter_client,
            embedding_client=embedding_client,
            operation_state=operation_state,
        ),
        workspace_id=settings.default_workspace_id,
        answer_mode=answer_mode,
    )


async def discover_dynamic_cases(client: ReadOnlySupabaseClient, workspace_id: str) -> list[AnswerQualityCase]:
    """Discover dynamic baseline cases from active read-only metadata."""
    cases: list[AnswerQualityCase] = []
    uploaded = await _discover_uploaded_material_case(client, workspace_id)
    cases.append(uploaded)
    mixed = await _discover_mixed_course_service_case(client, workspace_id)
    cases.append(mixed)
    archived = await _discover_archived_exclusion_case(client, workspace_id)
    cases.append(archived)
    cases.append(_discover_vision_case())
    return cases


async def run_answer_quality_case(runtime: AnswerQualityRuntime, case: AnswerQualityCase) -> AnswerQualityCaseResult:
    """Run one case through the real RAG answer path and return sanitized diagnostics."""
    if case.case_type.startswith("blocked_"):
        return _blocked_result(case)
    if case.case_type.startswith("skipped_"):
        return _skipped_result(case)

    started = time.monotonic()
    result = await runtime.pipeline.answer(
        case.question,
        workspace_id=runtime.workspace_id,
        dialog_context={"user_settings": {"answer_mode": runtime.answer_mode}},
    )
    duration = time.monotonic() - started
    hydration = await hydrate_documents_for_result(runtime.resources.supabase, runtime.workspace_id, result)
    return analyze_case_result(
        case=case,
        result=result,
        hydration=hydration,
        safety_state=runtime.resources.supabase.safety.snapshot(),
        duration_seconds=duration,
    )


async def hydrate_documents_for_result(
    client: ReadOnlySupabaseClient,
    workspace_id: str,
    result: "PipelineResult",
) -> dict[str, DocumentSummary]:
    """Hydrate selected/evidence/source document metadata through read-only selects."""
    ids = _document_ids_from_result(result)
    if not ids:
        return {}
    rows = await client.select(
        "documents",
        params={
            "select": "id,title,source_type,status,version,course,metadata",
            "workspace_id": f"eq.{workspace_id}",
            "id": f"in.({','.join(sorted(ids))})",
        },
    )
    summaries: dict[str, DocumentSummary] = {}
    for row in rows:
        document_id = str(row.get("id") or "")
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        source_type = str(row.get("source_type") or "")
        summaries[document_id] = DocumentSummary(
            document_id_hash=_hash_identifier(document_id),
            title=_sanitize_text(str(row.get("title") or ""), max_chars=180),
            source_type=source_type,
            source_class=_source_class(source_type),
            status=str(row.get("status") or ""),
            version=_safe_int(row.get("version")),
            course=_sanitize_text(str(row.get("course") or ""), max_chars=120),
            source_name=_sanitize_text(_metadata_source_name(metadata), max_chars=80),
            service_ids=_metadata_service_ids(metadata),
        )
    return summaries


def analyze_case_result(
    *,
    case: AnswerQualityCase,
    result: "PipelineResult",
    hydration: dict[str, DocumentSummary],
    safety_state: dict[str, object],
    duration_seconds: float = 0.0,
) -> AnswerQualityCaseResult:
    """Classify one pipeline result against case expectations."""
    debug = result.debug or {}
    selected_ids = _selected_document_ids(debug)
    accepted = _accepted_evidence(debug)
    selected_documents = [hydration[doc_id] for doc_id in selected_ids if doc_id in hydration]
    source_classes = sorted({doc.source_class for doc in selected_documents})
    selected_services = sorted(_service_ids_for_document_ids(selected_ids, hydration))
    accepted_document_ids = [
        str(item.get("document_id") or "")
        for item in accepted
        if item.get("document_id")
    ]
    source_document_ids = [source.document_id for source in result.sources if source.document_id]
    accepted_services = sorted(_service_ids_for_document_ids(accepted_document_ids, hydration))
    source_services = sorted(_service_ids_for_document_ids(source_document_ids, hydration))
    source_labels = SourceLabelBuilder().build_many(result.sources)
    final_answer = _sanitize_text(result.answer, max_chars=6000)
    evidence_summary = [_evidence_summary(item, hydration) for item in accepted]
    checks, expectation_diagnostics = _case_checks(
        case,
        result,
        hydration,
        selected_ids,
        accepted,
        source_labels,
    )
    warnings, failures, failure_codes = _case_warnings_and_failures(
        case,
        checks,
        expectation_diagnostics,
        result,
    )
    outcome = _case_outcome(case, warnings, failures)
    blocker_categories = _blocker_categories_for_case(
        case,
        outcome=outcome,
        failure_codes=failure_codes,
    )
    return AnswerQualityCaseResult(
        case_id=case.case_id,
        question=_sanitize_text(case.question, max_chars=500),
        outcome=outcome,
        pipeline_status=str(getattr(result.status, "value", result.status)),
        final_answer=final_answer,
        selected_documents=selected_documents,
        selected_source_classes=source_classes,
        selected_services=selected_services,
        detected_service_metadata=_sanitize_json(
            {
                "expected_service_ids": list(case.expected_service_ids),
                "service_expectation": case.service_expectation,
                "selected_service_ids": selected_services,
                "accepted_evidence_service_ids": accepted_services,
                "final_source_service_ids": source_services,
                "query_plan_domain_hint": (debug.get("query_plan") or {}).get("domain_hint")
                if isinstance(debug.get("query_plan"), dict)
                else "",
                "course_hint": debug.get("course_hint", ""),
            }
        ),
        accepted_evidence_summary=evidence_summary,
        final_sources=[_sanitize_text(label, max_chars=300) for label in source_labels],
        checks=checks,
        expectation_diagnostics=expectation_diagnostics,
        manual_review=_manual_review_stub(case, checks, source_classes),
        warnings=warnings,
        failures=failures,
        failure_codes=failure_codes,
        blocker_categories=blocker_categories,
        blocking_for_current_phase=case.blocking_for_current_phase,
        duration_seconds=round(duration_seconds, 3),
        model_metadata=_sanitize_json(
            {
                "llm_model_attempts": debug.get("llm_model_attempts", ()),
                "final_model_used": debug.get("final_model_used"),
                "fallback_used": debug.get("fallback_used", False),
                "answer_mode": debug.get("answer_mode", ""),
            }
        ),
        read_only_safety_state=safety_state,
    )


def classify_baseline(case_results: list[AnswerQualityCaseResult]) -> tuple[OverallClassification, PrimaryBlocker, str]:
    """Classify the overall baseline and select one primary next blocker."""
    if not case_results:
        return "incomplete_environment", "no_blocking_functional_gap", "Phase 7C-A remains active"

    active_categories = _active_blocker_categories(case_results)
    blocking = [
        case
        for case in case_results
        if case.outcome == "FAIL" and case.blocking_for_current_phase
    ]
    environment_blocked = [case for case in case_results if case.outcome == "BLOCKED"]
    if blocking:
        if len(active_categories) > 1:
            primary: PrimaryBlocker = "multiple_functional_blockers"
        elif active_categories:
            primary = active_categories[0]
        else:
            primary = "unclassified_functional_gap"
        return "functional_blocker_found", primary, f"Phase 7C-B - one focused fix for {primary}"
    if environment_blocked:
        return "incomplete_environment", "no_blocking_functional_gap", "Phase 7C-A remains active"
    return "baseline_pass", "no_blocking_functional_gap", "Phase 8A - Uploaded File Lifecycle and Storage Hygiene"


async def capture_production_safety_snapshot(
    client: ReadOnlySupabaseClient,
) -> dict[str, TableStateSnapshot]:
    """Capture read-only table fingerprints without serializing source rows."""
    snapshots: dict[str, TableStateSnapshot] = {}
    documents_rows: list[dict[str, Any]] | None = None
    for table, fields in SAFETY_SNAPSHOT_FIELDS.items():
        snapshot, rows = await _capture_table_state(client, table, fields)
        snapshots[table] = snapshot
        if table == "documents":
            documents_rows = rows

    if documents_rows is None:
        snapshots["document_versions"] = _incomplete_table_snapshot(
            snapshots["documents"].error_code or "documents_snapshot_incomplete",
            source_relation="documents",
        )
    else:
        version_rows = [
            {field_name: row.get(field_name) for field_name in DOCUMENT_VERSION_FIELDS}
            for row in documents_rows
        ]
        snapshots["document_versions"] = _complete_table_snapshot(
            version_rows,
            source_relation="documents",
        )
    return {name: snapshots[name] for name in SAFETY_SNAPSHOT_NAMES}


def build_production_safety_telemetry(
    *,
    safety_state: dict[str, object],
    operation_state: HarnessOperationState,
    before_snapshots: dict[str, TableStateSnapshot],
    after_snapshots: dict[str, TableStateSnapshot],
) -> ProductionSafetyTelemetry:
    """Build explicit operation counters and before/after state comparisons."""
    comparisons: dict[str, TableSafetyComparison] = {}
    changed_values: dict[str, ChangedState] = {}
    for name in SAFETY_SNAPSHOT_NAMES:
        source_relation = "documents" if name == "document_versions" else name
        before = before_snapshots.get(name) or _incomplete_table_snapshot(
            "before_snapshot_missing",
            source_relation=source_relation,
        )
        after = after_snapshots.get(name) or _incomplete_table_snapshot(
            "after_snapshot_missing",
            source_relation=source_relation,
        )
        comparison = _compare_table_snapshots(before, after)
        comparisons[name] = comparison
        changed_values[name] = _comparison_changed_value(comparison.comparison)

    raw_rpc_counts = safety_state.get("allowed_rpc_calls_by_name")
    if isinstance(raw_rpc_counts, dict):
        rpc_counts = {
            str(name): int(count)
            for name, count in sorted(raw_rpc_counts.items())
            if str(name)
        }
    else:
        rpc_names = [
            str(value)
            for value in safety_state.get("allowed_rpc_names", [])
            if str(value)
        ]
        rpc_counts = dict(sorted(Counter(rpc_names).items()))
    write_attempts = int(safety_state.get("write_attempts", 0))
    blocked_write_attempts = int(safety_state.get("blocked_write_calls", 0))
    unknown_rpc_attempts = int(safety_state.get("non_allowlisted_rpc_attempts", 0))
    counter_provenance = _counter_provenance_from_operation_state(operation_state)
    safety_result = _compute_safety_result(
        counters={
            "unknown_rpc_attempts": unknown_rpc_attempts,
            "supabase_write_attempts": write_attempts,
            "blocked_write_attempts": blocked_write_attempts,
            "evidence_log_write_attempts": operation_state.evidence_log_write_attempts,
            "telegram_message_attempts": operation_state.telegram_message_attempts,
            "external_docs_operation_attempts": operation_state.external_docs_operation_attempts,
        },
        flags={
            "evidence_logging_disabled": operation_state.evidence_logging_disabled,
            "telegram_sending_disabled": operation_state.telegram_sending_disabled,
            "external_docs_operations_disabled": operation_state.external_docs_operations_disabled,
            "read_only_adapter_enabled": operation_state.read_only_adapter_enabled,
            "atomic_output_enabled": operation_state.atomic_output_enabled,
            "manual_env_access_performed": operation_state.manual_env_access_performed,
            "secret_values_rendered": operation_state.secret_values_rendered,
            "settings_loader_attempted": operation_state.settings_loader_attempted,
            "settings_loader_used": operation_state.settings_loader_used,
        },
        comparisons={name: comparison.comparison for name, comparison in comparisons.items()},
        counter_provenance=counter_provenance,
    )
    return ProductionSafetyTelemetry(
        safety_result=safety_result,
        select_calls=int(safety_state.get("allowed_selects", 0)),
        allowlisted_rpc_calls_total=int(safety_state.get("allowed_rpc_calls", 0)),
        allowlisted_rpc_calls_by_name=rpc_counts,
        unknown_rpc_attempts=unknown_rpc_attempts,
        supabase_write_attempts=write_attempts,
        blocked_write_attempts=blocked_write_attempts,
        evidence_log_write_attempts=operation_state.evidence_log_write_attempts,
        telegram_message_attempts=operation_state.telegram_message_attempts,
        external_docs_operation_attempts=operation_state.external_docs_operation_attempts,
        model_attempts=operation_state.model_attempts,
        evidence_logging_disabled=operation_state.evidence_logging_disabled,
        telegram_sending_disabled=operation_state.telegram_sending_disabled,
        read_only_adapter_enabled=operation_state.read_only_adapter_enabled,
        atomic_output_enabled=operation_state.atomic_output_enabled,
        manual_env_access_performed=operation_state.manual_env_access_performed,
        secret_values_rendered=operation_state.secret_values_rendered,
        settings_loader_attempted=operation_state.settings_loader_attempted,
        settings_loader_used=operation_state.settings_loader_used,
        external_docs_operations_disabled=operation_state.external_docs_operations_disabled,
        counter_provenance=counter_provenance,
        table_snapshots=comparisons,
        documents_changed=changed_values["documents"],
        document_versions_changed=changed_values["document_versions"],
        sections_changed=changed_values["sections"],
        chunks_changed=changed_values["chunks"],
        conversations_changed=changed_values["conversations"],
        messages_changed=changed_values["messages"],
        term_statistics_changed=changed_values["term_statistics"],
    )


def baseline_from_results(
    *,
    case_results: list[AnswerQualityCaseResult],
    git_sha: str,
    workspace_id: str,
    safety_state: dict[str, object],
    operation_state: HarnessOperationState | None = None,
    before_snapshots: dict[str, TableStateSnapshot] | None = None,
    after_snapshots: dict[str, TableStateSnapshot] | None = None,
    run_state: RunState = "completed",
) -> AnswerQualityBaseline:
    """Build the top-level baseline artifact."""
    overall, primary, next_phase = classify_baseline(case_results)
    operation_state = operation_state or HarnessOperationState()
    telemetry = build_production_safety_telemetry(
        safety_state=safety_state,
        operation_state=operation_state,
        before_snapshots=before_snapshots or {},
        after_snapshots=after_snapshots or {},
    )
    return AnswerQualityBaseline(
        schema_version=HARNESS_SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        git_sha=git_sha,
        workspace=_hash_identifier(workspace_id),
        evidence_logging_disabled=operation_state.evidence_logging_disabled,
        telegram_sending_disabled=operation_state.telegram_sending_disabled,
        supabase_write_attempts=int(safety_state.get("write_attempts", 0)),
        blocked_write_calls=int(safety_state.get("blocked_write_calls", 0)),
        non_allowlisted_rpc_attempts=int(safety_state.get("non_allowlisted_rpc_attempts", 0)),
        case_results=case_results,
        overall_classification=overall,
        primary_blocker=primary,
        recommended_next_phase=next_phase,
        active_blocker_categories=_active_blocker_categories(case_results),
        production_safety_telemetry=telemetry,
        read_only_safety_state=safety_state,
        run_state=run_state,
    )


async def _capture_table_state(
    client: ReadOnlySupabaseClient,
    table: str,
    fields: tuple[str, ...],
) -> tuple[TableStateSnapshot, list[dict[str, Any]] | None]:
    try:
        rows = await _select_all_snapshot_rows(client, table, fields)
    except Exception as exc:  # noqa: BLE001 - artifact stores only a sanitized error code
        return _incomplete_table_snapshot(
            _snapshot_error_code(exc),
            source_relation=table,
        ), None
    return _complete_table_snapshot(rows, source_relation=table), rows


async def _select_all_snapshot_rows(
    client: ReadOnlySupabaseClient,
    table: str,
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page_size = 500
    offset = 0
    while True:
        page = await client.select(
            table,
            params={
                "select": ",".join(fields),
                "order": "id.asc",
                "limit": str(page_size),
                "offset": str(offset),
            },
        )
        rows.extend(row for row in page if isinstance(row, dict))
        if len(page) < page_size:
            return rows
        offset += page_size


def _complete_table_snapshot(
    rows: list[dict[str, Any]],
    *,
    source_relation: str = "",
) -> TableStateSnapshot:
    canonical_rows = sorted(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        for row in rows
    )
    digest = hashlib.sha256()
    for row in canonical_rows:
        encoded = row.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return TableStateSnapshot(
        row_count=len(rows),
        safe_metadata_digest=digest.hexdigest(),
        snapshot_status="complete",
        source_relation=source_relation,
    )


def _incomplete_table_snapshot(
    error_code: str,
    *,
    source_relation: str = "",
) -> TableStateSnapshot:
    return TableStateSnapshot(
        row_count=None,
        safe_metadata_digest="",
        snapshot_status="incomplete",
        error_code=_sanitize_text(error_code, max_chars=120),
        source_relation=source_relation,
    )


def _compare_table_snapshots(
    before: TableStateSnapshot,
    after: TableStateSnapshot,
) -> TableSafetyComparison:
    if before.snapshot_status != "complete" or after.snapshot_status != "complete":
        comparison: SnapshotComparison = "incomplete"
    elif (
        before.row_count == after.row_count
        and before.safe_metadata_digest == after.safe_metadata_digest
    ):
        comparison = "unchanged"
    else:
        comparison = "changed"
    return TableSafetyComparison(before=before, after=after, comparison=comparison)


def _comparison_changed_value(comparison: SnapshotComparison) -> ChangedState:
    if comparison == "changed":
        return True
    if comparison == "unchanged":
        return False
    return "unknown"


def _counter_provenance_from_operation_state(
    operation_state: HarnessOperationState,
) -> dict[str, CounterProvenanceMode]:
    return {
        "evidence_log_write_attempts": "disabled_by_construction"
        if operation_state.evidence_logging_disabled
        else "instrumented",
        "telegram_message_attempts": "disabled_by_construction"
        if operation_state.telegram_sending_disabled
        else "instrumented",
        "external_docs_operation_attempts": "disabled_by_construction"
        if operation_state.external_docs_operations_disabled
        else "instrumented",
    }


def _compute_safety_result(
    *,
    counters: dict[str, int],
    flags: dict[str, bool],
    comparisons: dict[str, SnapshotComparison],
    counter_provenance: dict[str, CounterProvenanceMode],
) -> SafetyResult:
    hard_failure = bool(
        counters.get("supabase_write_attempts", 0)
        or counters.get("blocked_write_attempts", 0)
        or counters.get("unknown_rpc_attempts", 0)
        or counters.get("evidence_log_write_attempts", 0)
        or counters.get("telegram_message_attempts", 0)
        or counters.get("external_docs_operation_attempts", 0)
        or flags.get("manual_env_access_performed", False)
        or flags.get("secret_values_rendered", False)
        or not flags.get("evidence_logging_disabled", False)
        or not flags.get("telegram_sending_disabled", False)
        or not flags.get("external_docs_operations_disabled", False)
        or not flags.get("read_only_adapter_enabled", False)
        or not flags.get("atomic_output_enabled", False)
        or any(value == "changed" for value in comparisons.values())
    )
    if hard_failure:
        return "FAIL"

    provenance_confirmed = _counter_provenance_confirmed(
        counter_provenance=counter_provenance,
        flags=flags,
    )
    complete_runtime_boundary = (
        flags.get("settings_loader_attempted", False)
        and flags.get("settings_loader_used", False)
        and provenance_confirmed
    )
    if (
        not complete_runtime_boundary
        or any(value == "incomplete" for value in comparisons.values())
    ):
        return "BLOCKED"
    return "PASS"


def _counter_provenance_confirmed(
    *,
    counter_provenance: dict[str, CounterProvenanceMode],
    flags: dict[str, bool],
) -> bool:
    if set(counter_provenance) != set(COUNTER_PROVENANCE_FIELDS):
        return False
    for field_name, mode in counter_provenance.items():
        if mode not in VALID_COUNTER_PROVENANCE_MODES:
            return False
        if mode == "disabled_by_construction":
            if field_name == "evidence_log_write_attempts" and not flags.get("evidence_logging_disabled", False):
                return False
            if field_name == "telegram_message_attempts" and not flags.get("telegram_sending_disabled", False):
                return False
            if field_name == "external_docs_operation_attempts" and not flags.get("external_docs_operations_disabled", False):
                return False
    return True


def _snapshot_error_code(exc: Exception) -> str:
    if isinstance(exc, SupabaseRequestError):
        return f"supabase_http_{exc.status_code}"
    if isinstance(exc, TimeoutError):
        return "timeout"
    name = type(exc).__name__
    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_") or "snapshot_error"


def save_baseline_atomic(path: Path, baseline: AnswerQualityBaseline) -> None:
    """Write a UTF-8 JSON baseline artifact atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dataclass_to_sanitized_dict(baseline)
    if isinstance(payload, dict):
        telemetry = payload.get("production_safety_telemetry")
        if isinstance(telemetry, dict):
            rendered_secret = bool(telemetry.get("secret_values_rendered")) or _contains_secret_like(
                json.dumps(payload, ensure_ascii=False, sort_keys=True)
            )
            baseline.production_safety_telemetry.secret_values_rendered = rendered_secret
            telemetry["secret_values_rendered"] = rendered_secret
            if rendered_secret:
                baseline.production_safety_telemetry.safety_result = "FAIL"
                telemetry["safety_result"] = "FAIL"
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def load_existing_baseline_for_resume(path: Path) -> AnswerQualityBaseline | None:
    """Load a completed schema-v3 artifact only after fail-closed validation."""
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    schema_version = data.get("schema_version") if isinstance(data, dict) else None
    if schema_version != HARNESS_SCHEMA_VERSION:
        raise ValueError(
            "Cannot resume answer-quality results from a different harness schema: "
            f"{schema_version or 'missing'}"
        )
    if not isinstance(data, dict):
        raise ValueError("Cannot resume malformed schema v3 artifact")
    return _validate_completed_v3_resume_artifact(data)


def load_existing_case_results(path: Path) -> list[AnswerQualityCaseResult]:
    """Load previously completed case results for resume mode."""
    baseline = load_existing_baseline_for_resume(path)
    return list(baseline.case_results) if baseline else []


def dataclass_to_sanitized_dict(value: object) -> object:
    """Convert dataclasses to sanitized JSON-compatible values."""
    return _sanitize_json(asdict(value) if hasattr(value, "__dataclass_fields__") else value)


def _case_checks(
    case: AnswerQualityCase,
    result: "PipelineResult",
    hydration: dict[str, DocumentSummary],
    selected_ids: list[str],
    accepted: list[dict[str, Any]],
    source_labels: list[str],
) -> tuple[CaseChecks, dict[str, object]]:
    answer = result.answer or ""
    status = str(getattr(result.status, "value", result.status))
    debug = result.debug or {}
    answer_mode = str(debug.get("answer_mode") or "")
    selected_active = all(
        hydration.get(doc_id, DocumentSummary("", "", "", "", "")).status == "active"
        for doc_id in selected_ids
    )
    accepted_doc_ids = {
        str(item.get("document_id") or "")
        for item in accepted
        if item.get("document_id")
    }
    accepted_active = all(
        hydration.get(doc_id, DocumentSummary("", "", "", "", "")).status == "active"
        for doc_id in accepted_doc_ids
    )
    accepted_evidence_ids = {str(item.get("evidence_id") or "") for item in accepted}
    sources_from_accepted = all(
        bool(source.evidence_id) and str(source.evidence_id) in accepted_evidence_ids
        for source in result.sources
    )
    source_document_ids = {source.document_id for source in result.sources if source.document_id}
    accepted_services = _service_ids_for_document_ids(accepted_doc_ids, hydration)
    source_services = _service_ids_for_document_ids(source_document_ids, hydration)
    support_services = accepted_services | source_services
    accepted_source_classes = _source_classes_for_document_ids(accepted_doc_ids, hydration)
    source_source_classes = _source_classes_for_document_ids(source_document_ids, hydration)
    support_source_classes = accepted_source_classes | source_source_classes
    expected_services = {_normalize_service_id(value) for value in case.expected_service_ids if value}
    unexpected_services = support_services - expected_services if expected_services else set()
    service_expectation_met = True
    if case.service_expectation == "required":
        service_expectation_met = bool(expected_services & support_services) and not unexpected_services
    elif case.service_expectation == "forbid_confident":
        service_expectation_met = not support_services

    term_evidence = accepted
    if case.service_expectation == "required":
        term_evidence = [
            item
            for item in accepted
            if _service_ids_for_document_ids(
                [str(item.get("document_id") or "")],
                hydration,
            )
            & expected_services
        ]
    required_groups = list(case.required_evidence_term_groups)
    if case.expected_terms:
        required_groups.append(case.expected_terms)
    required_group_results = _evaluate_evidence_term_groups(required_groups, term_evidence)
    optional_group_results = _evaluate_evidence_term_groups(
        case.optional_evidence_term_groups,
        term_evidence,
    )
    required_groups_met = all(bool(group["satisfied"]) for group in required_group_results)
    optional_groups_met = all(bool(group["satisfied"]) for group in optional_group_results)
    expected_terms_present = required_groups_met
    evidence_required = bool(
        case.requires_accepted_evidence
        or required_groups
        or case.expected_source_types
        or case.requires_mixed_sources
        or case.service_expectation == "required"
    )
    required_source_types_present = set(case.expected_source_types).issubset(
        support_source_classes
    )
    if case.requires_mixed_sources:
        required_source_types_present = required_source_types_present and {
            SOURCE_CLASS_UPLOADED,
            SOURCE_CLASS_EXTERNAL,
        }.issubset(support_source_classes)
    residue_absent = not _contains_residue(answer)
    checks = CaseChecks(
        answer_non_empty=bool(answer.strip()),
        status_allowed=status in case.allowed_statuses,
        answer_mode_allowed=not case.allowed_answer_modes or answer_mode in case.allowed_answer_modes,
        selected_documents_active=selected_active,
        accepted_evidence_present=bool(accepted),
        accepted_evidence_active=accepted_active,
        sources_from_accepted_evidence=sources_from_accepted,
        archived_absent=all(doc.status != "archived" for doc in hydration.values()),
        required_source_types_present=required_source_types_present,
        service_expectation_met=service_expectation_met,
        required_evidence_groups_met=required_groups_met,
        optional_evidence_groups_met=optional_groups_met,
        no_raw_uuid_in_answer=UUID_RE.search(answer) is None,
        no_debug_metadata_in_answer=not any(token in answer for token in ("chunk_id", "document_id", "evidence_id", "{'debug'", '"debug"', "metadata")),
        no_evidence_block_leak="Evidence:" not in answer and "Accepted evidence" not in answer,
        no_secret_like_text=not _contains_secret_like(answer + "\n" + "\n".join(source_labels)),
        no_residue_in_answer=residue_absent,
        expected_terms_present=expected_terms_present,
        insufficient_evidence_handled=_insufficient_evidence_handled(case, result),
    )
    diagnostics = _sanitize_json(
        {
            "answer_mode": answer_mode,
            "evidence_required": evidence_required,
            "expected_service_ids": sorted(expected_services),
            "selected_service_ids": sorted(
                _service_ids_for_document_ids(selected_ids, hydration)
            ),
            "accepted_evidence_service_ids": sorted(accepted_services),
            "final_source_service_ids": sorted(source_services),
            "unexpected_service_ids": sorted(unexpected_services),
            "accepted_evidence_source_classes": sorted(accepted_source_classes),
            "final_source_classes": sorted(source_source_classes),
            "required_evidence_term_groups": required_group_results,
            "optional_evidence_term_groups": optional_group_results,
        }
    )
    return checks, diagnostics


def _case_warnings_and_failures(
    case: AnswerQualityCase,
    checks: CaseChecks,
    diagnostics: dict[str, object],
    result: "PipelineResult",
) -> tuple[list[str], list[str], list[FailureCode]]:
    return _case_warnings_and_failures_from_status(
        case,
        checks,
        diagnostics,
        str(getattr(result.status, "value", result.status)),
    )


def _case_warnings_and_failures_from_status(
    case: AnswerQualityCase,
    checks: CaseChecks,
    diagnostics: dict[str, object],
    pipeline_status: str,
) -> tuple[list[str], list[str], list[FailureCode]]:
    warnings: list[str] = []
    failures: list[str] = []
    failure_codes: list[FailureCode] = []
    evidence_required = bool(diagnostics.get("evidence_required"))
    if not checks.answer_non_empty:
        _add_failure(failures, failure_codes, "final answer is empty", "final_answer_missing")
    if not checks.status_allowed:
        _add_failure(
            failures,
            failure_codes,
            "pipeline status is outside the case expectation",
            "status_expectation_mismatch",
        )
    if not checks.answer_mode_allowed:
        _add_failure(
            failures,
            failure_codes,
            "answer mode is outside the case expectation",
            "answer_mode_mismatch",
        )
    if not checks.selected_documents_active:
        _add_failure(
            failures,
            failure_codes,
            "selected document metadata did not confirm active status",
            "inactive_selected_document",
        )
    if not checks.accepted_evidence_active:
        _add_failure(
            failures,
            failure_codes,
            "accepted evidence did not hydrate to active documents",
            "inactive_accepted_evidence",
        )
    if evidence_required and not checks.accepted_evidence_present:
        _add_failure(
            failures,
            failure_codes,
            "required accepted evidence is missing",
            "required_evidence_missing",
        )
    if not checks.sources_from_accepted_evidence:
        _add_failure(
            failures,
            failure_codes,
            "final sources are not limited to accepted evidence",
            "source_evidence_mismatch",
        )
    if case.check_archived_exclusion and not checks.archived_absent:
        _add_failure(
            failures,
            failure_codes,
            "archived evidence appeared in selected or accepted evidence",
            "archived_evidence_present",
        )
    if not checks.no_raw_uuid_in_answer or not checks.no_debug_metadata_in_answer or not checks.no_evidence_block_leak:
        _add_failure(
            failures,
            failure_codes,
            "internal identifiers or debug metadata leaked into the answer",
            "internal_metadata_leak",
        )
    if not checks.no_secret_like_text:
        _add_failure(
            failures,
            failure_codes,
            "secret-like token appeared in the sanitized report",
            "secret_like_output",
        )
    if not checks.no_residue_in_answer:
        _add_failure(
            failures,
            failure_codes,
            "dirty documentation residue entered the final answer",
            "documentation_residue_present",
        )
    if not checks.required_source_types_present:
        if case.requires_mixed_sources:
            message = "mixed uploaded/course and external evidence was not accepted"
        elif case.expected_source_types:
            message = "expected source class was not present in accepted evidence or final sources"
        else:
            message = ""
        if message:
            _add_failure(
                failures,
                failure_codes,
                message,
                "required_source_class_missing",
            )
    if not checks.service_expectation_met:
        if case.service_expectation == "forbid_confident":
            _add_failure(
                failures,
                failure_codes,
                "ambiguous service case used service-specific accepted evidence or sources",
                "forbid_confident_service_violation",
            )
        else:
            expected = set(_string_list(diagnostics.get("expected_service_ids")))
            selected = set(_string_list(diagnostics.get("selected_service_ids")))
            support = set(_string_list(diagnostics.get("accepted_evidence_service_ids"))) | set(
                _string_list(diagnostics.get("final_source_service_ids"))
            )
            unexpected = set(_string_list(diagnostics.get("unexpected_service_ids")))
            if unexpected or (support and not expected.intersection(support)):
                code: FailureCode = "wrong_service_evidence"
            elif expected and not expected.intersection(selected):
                code = "required_service_missing"
            else:
                code = "required_evidence_missing"
            _add_failure(
                failures,
                failure_codes,
                "accepted evidence or final sources did not match the expected service",
                code,
            )
    if not checks.required_evidence_groups_met:
        _add_failure(
            failures,
            failure_codes,
            "required high-signal evidence term groups were not satisfied",
            "required_evidence_group_missing",
        )
    if not checks.optional_evidence_groups_met:
        warnings.append("optional evidence term groups were not satisfied")
    if case.case_type == "out_of_base" and pipeline_status == "answered":
        _add_failure(
            failures,
            failure_codes,
            "out-of-base question received a confident answered status",
            "answered_without_evidence",
        )
    if case.expected_limitation and not checks.insufficient_evidence_handled:
        warnings.append("expected limitation was not clearly handled")
    if failures and not case.blocking_for_current_phase:
        failure_codes = ["known_deferred_limitation"]
    return warnings, failures, list(dict.fromkeys(failure_codes))


def _case_outcome(case: AnswerQualityCase, warnings: list[str], failures: list[str]) -> CaseOutcome:
    if case.case_type.startswith("blocked_"):
        return "BLOCKED"
    if case.case_type.startswith("skipped_"):
        return "SKIPPED"
    if failures:
        return "FAIL"
    if warnings:
        return "WARN"
    return "PASS"


def _manual_review_stub(case: AnswerQualityCase, checks: CaseChecks, source_classes: list[str]) -> dict[str, object]:
    return {
        "correct_course_or_material_found": SOURCE_CLASS_UPLOADED in source_classes,
        "correct_service_found": checks.service_expectation_met,
        "required_evidence_present": checks.accepted_evidence_present,
        "required_evidence_groups_met": checks.required_evidence_groups_met,
        "required_source_classes_present": checks.required_source_types_present,
        "answer_practical_and_understandable": checks.answer_non_empty,
        "sources_readable": True,
        "internal_data_leaked": not checks.no_raw_uuid_in_answer or not checks.no_debug_metadata_in_answer,
        "dirty_residue_affected_answer": not checks.no_residue_in_answer,
        "insufficient_evidence_handled": checks.insufficient_evidence_handled,
    }


def _add_failure(
    failures: list[str],
    failure_codes: list[FailureCode],
    message: str,
    code: FailureCode,
) -> None:
    failures.append(message)
    failure_codes.append(code)


def _blocker_categories_for_case(
    case: AnswerQualityCase,
    *,
    outcome: CaseOutcome,
    failure_codes: list[FailureCode],
) -> list[BlockerCategory]:
    if outcome != "FAIL":
        return []
    if not case.blocking_for_current_phase:
        return ["known_deferred_limitation"]

    codes = set(failure_codes)
    categories: list[BlockerCategory] = []
    routing_or_lifecycle_gap = False
    if "forbid_confident_service_violation" in codes:
        categories.append("ambiguous_service_routing_gap")
        routing_or_lifecycle_gap = True
    if "required_source_class_missing" in codes:
        expected_classes = set(case.expected_source_types)
        if SOURCE_CLASS_UPLOADED in expected_classes and not case.requires_mixed_sources:
            categories.append("uploaded_material_routing_gap")
            routing_or_lifecycle_gap = True
        elif case.requires_mixed_sources:
            categories.append("mixed_source_allocation_gap")
            routing_or_lifecycle_gap = True
    if {"required_service_missing", "wrong_service_evidence"} & codes:
        categories.append("explicit_service_routing_gap")
        routing_or_lifecycle_gap = True
    if "archived_evidence_present" in codes:
        categories.append("archived_evidence_gap")
        routing_or_lifecycle_gap = True
    if not routing_or_lifecycle_gap and {
        "required_evidence_missing",
        "required_evidence_group_missing",
        "inactive_selected_document",
        "inactive_accepted_evidence",
    } & codes:
        categories.append("evidence_selection_gap")
    if "answered_without_evidence" in codes:
        categories.append("grounding_gap")
    if "final_answer_missing" in codes:
        categories.append("answer_generation_gap")
    if {"source_evidence_mismatch", "internal_metadata_leak"} & codes:
        categories.append("citation_source_label_gap")
    if "documentation_residue_present" in codes:
        categories.append("documentation_residue_affects_answers")
    if not categories and {"status_expectation_mismatch", "answer_mode_mismatch"} & codes:
        categories.append("answer_status_gap")
    if not categories and codes:
        categories.append("unclassified_functional_gap")
    return list(dict.fromkeys(categories))


def _active_blocker_categories(
    case_results: list[AnswerQualityCaseResult],
) -> list[BlockerCategory]:
    active = {
        category
        for case in case_results
        if case.outcome == "FAIL" and case.blocking_for_current_phase
        for category in case.blocker_categories
        if category != "known_deferred_limitation"
    }
    return sorted(active)


async def _discover_uploaded_material_case(client: ReadOnlySupabaseClient, workspace_id: str) -> AnswerQualityCase:
    rows = await client.select(
        "documents",
        params={
            "select": "id,title,course,source_type,status,metadata",
            "workspace_id": f"eq.{workspace_id}",
            "status": "eq.active",
            "source_type": "neq.external_docs",
            "limit": "20",
            "order": "updated_at.desc",
        },
    )
    for row in rows:
        doc_id = str(row.get("id") or "")
        if not doc_id:
            continue
        cards = await client.select(
            "document_cards",
            params={
                "select": "questions_answered,topics,summary,metadata",
                "workspace_id": f"eq.{workspace_id}",
                "document_id": f"eq.{doc_id}",
                "limit": "1",
            },
        )
        question = _first_safe_card_question(cards)
        if question:
            return AnswerQualityCase(
                case_id="uploaded_material_only_auto",
                question=question,
                case_type="uploaded_material_only",
                expected_source_types=(SOURCE_CLASS_UPLOADED,),
                requires_accepted_evidence=True,
                notes=_sanitize_text(str(row.get("title") or ""), max_chars=160),
            )
    return AnswerQualityCase(
        case_id="uploaded_material_only_auto",
        question="",
        case_type="blocked_no_suitable_uploaded_material",
        notes="No active non-external document card with a safe represented question was found.",
    )


async def _discover_mixed_course_service_case(client: ReadOnlySupabaseClient, workspace_id: str) -> AnswerQualityCase:
    external_rows = await client.select(
        "documents",
        params={
            "select": "title,source_type,status,metadata",
            "workspace_id": f"eq.{workspace_id}",
            "status": "eq.active",
            "source_type": "eq.external_docs",
            "limit": "100",
        },
    )
    service_names = {
        _metadata_source_name(row.get("metadata") if isinstance(row.get("metadata"), dict) else {}).lower()
        for row in external_rows
    }
    service_names = {name for name in service_names if name}
    if not service_names:
        return AnswerQualityCase(
            case_id="mixed_course_service_auto",
            question="",
            case_type="blocked_no_mixed_source_fixture",
            notes="No active external documentation service metadata was found.",
        )

    uploaded = await client.select(
        "documents",
        params={
            "select": "id,title,course,source_type,status,metadata",
            "workspace_id": f"eq.{workspace_id}",
            "status": "eq.active",
            "source_type": "neq.external_docs",
            "limit": "50",
        },
    )
    for row in uploaded:
        doc_id = str(row.get("id") or "")
        cards = await client.select(
            "document_cards",
            params={
                "select": "questions_answered,topics,entities,summary,metadata",
                "workspace_id": f"eq.{workspace_id}",
                "document_id": f"eq.{doc_id}",
                "limit": "1",
            },
        )
        haystack = " ".join(_card_terms(cards)).lower()
        for service in sorted(service_names):
            if service and service in haystack:
                base_question = _first_safe_card_question(cards) or f"что важно учесть по теме {service}?"
                context = str(row.get("course") or row.get("title") or "материала")
                return AnswerQualityCase(
                    case_id="mixed_course_service_auto",
                    question=f"В контексте {context}: {base_question} Проверь также официальную документацию {service}.",
                    case_type="mixed_course_service",
                    expected_service_ids=(_normalize_service_id(service),),
                    service_expectation="required",
                    expected_source_types=(SOURCE_CLASS_UPLOADED, SOURCE_CLASS_EXTERNAL),
                    requires_accepted_evidence=True,
                    requires_mixed_sources=True,
                )
    return AnswerQualityCase(
        case_id="mixed_course_service_auto",
        question="",
        case_type="blocked_no_mixed_source_fixture",
        notes="No active uploaded material mentioning a service with active external docs was found.",
    )


async def _discover_archived_exclusion_case(client: ReadOnlySupabaseClient, workspace_id: str) -> AnswerQualityCase:
    rows = await client.select(
        "documents",
        params={
            "select": "title,source_type,status,metadata",
            "workspace_id": f"eq.{workspace_id}",
            "status": "eq.archived",
            "limit": "20",
        },
    )
    if not rows:
        return AnswerQualityCase(
            case_id="archived_exclusion",
            question="",
            case_type="blocked_no_archived_fixture",
            notes="No archived documents found for generic exclusion audit.",
        )
    for row in rows:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        service = _metadata_source_name(metadata)
        if service:
            return AnswerQualityCase(
                case_id="archived_exclusion",
                question=f"как использовать {service} API?",
                case_type="archived_exclusion",
                expected_service_ids=(service,),
                check_archived_exclusion=True,
            )
    title = str(rows[0].get("title") or "материал")
    return AnswerQualityCase(
        case_id="archived_exclusion",
        question=f"что говорится в материале {title[:80]}?",
        case_type="archived_exclusion",
        check_archived_exclusion=True,
    )


def _discover_vision_case() -> AnswerQualityCase:
    safe_candidates = [Path("tests/fixtures/vision.png"), Path("tests/fixtures/test-image.png")]
    if not any(path.exists() for path in safe_candidates):
        return AnswerQualityCase(
            case_id="vision_optional",
            question="",
            case_type="skipped_vision_not_available",
            notes="No configured safe local test image was found.",
        )
    return AnswerQualityCase(
        case_id="vision_optional",
        question="опиши изображение и ответь только если есть достаточно данных",
        case_type="skipped_vision_not_available",
        notes="Vision audit is intentionally skipped until a no-Telegram image path is wired.",
    )


def _blocked_result(case: AnswerQualityCase) -> AnswerQualityCaseResult:
    return AnswerQualityCaseResult(
        case_id=case.case_id,
        question=case.question,
        outcome="BLOCKED",
        warnings=[case.notes or case.case_type],
        failure_codes=["fixture_missing"],
        blocker_categories=[],
        blocking_for_current_phase=case.blocking_for_current_phase,
        manual_review={"blocked_reason": case.case_type},
    )


def _skipped_result(case: AnswerQualityCase) -> AnswerQualityCaseResult:
    return AnswerQualityCaseResult(
        case_id=case.case_id,
        question=case.question,
        outcome="SKIPPED",
        warnings=[case.notes or case.case_type],
        blocker_categories=[],
        blocking_for_current_phase=False,
        manual_review={"skipped_reason": case.case_type},
    )


def _document_ids_from_result(result: "PipelineResult") -> set[str]:
    ids = set(_selected_document_ids(result.debug or {}))
    for item in _accepted_evidence(result.debug or {}):
        doc_id = str(item.get("document_id") or "")
        if doc_id:
            ids.add(doc_id)
    for source in result.sources:
        if source.document_id:
            ids.add(source.document_id)
    return ids


def _selected_document_ids(debug: dict[str, object]) -> list[str]:
    rows = debug.get("selected_documents", [])
    ids: list[str] = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("document_id"):
                ids.append(str(row["document_id"]))
    return ids


def _accepted_evidence(debug: dict[str, object]) -> list[dict[str, Any]]:
    rows = debug.get("accepted_evidence", [])
    return [row for row in rows if isinstance(row, dict)]


def _evidence_summary(item: dict[str, Any], hydration: dict[str, DocumentSummary]) -> dict[str, object]:
    doc_id = str(item.get("document_id") or "")
    text = str(item.get("text") or "")
    doc = hydration.get(doc_id)
    return _sanitize_json(
        {
            "evidence_id_hash": _hash_identifier(str(item.get("evidence_id") or "")),
            "document_id_hash": _hash_identifier(doc_id),
            "document_title": doc.title if doc else "",
            "source_class": doc.source_class if doc else SOURCE_CLASS_UNKNOWN,
            "service_ids": list(doc.service_ids) if doc else [],
            "locator": item.get("locator"),
            "score": item.get("score"),
            "preview": _sanitize_text(text, max_chars=300),
            "residue_signals": _residue_signals(text),
        }
    )


def _term_in_evidence(term: str, accepted: list[dict[str, Any]]) -> bool:
    return any(
        _expectation_term_matches(
            term,
            " ".join(
                [
                    str(item.get("locator") or ""),
                    str(item.get("text") or ""),
                ]
            ),
        )
        for item in accepted
    )


def _evaluate_evidence_term_groups(
    groups: list[tuple[str, ...]] | tuple[tuple[str, ...], ...],
    accepted: list[dict[str, Any]],
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for group in groups:
        matched = [term for term in group if _term_in_evidence(term, accepted)]
        results.append(
            {
                "alternatives": list(group),
                "matched_terms": matched,
                "satisfied": bool(matched),
            }
        )
    return results


def _validate_completed_v3_resume_artifact(data: dict[str, Any]) -> AnswerQualityBaseline:
    run_state = data.get("run_state")
    if run_state != "completed":
        raise ValueError("Cannot resume schema v3 artifact unless run_state is completed")
    telemetry = _validate_v3_telemetry_contract(data)
    case_results = _validate_v3_case_results(data)
    overall, primary, next_phase = classify_baseline(case_results)
    active_categories = _active_blocker_categories(case_results)
    if data.get("overall_classification") != overall:
        raise ValueError("Cannot resume schema v3 artifact with inconsistent overall classification")
    if data.get("primary_blocker") != primary:
        raise ValueError("Cannot resume schema v3 artifact with inconsistent primary blocker")
    if data.get("recommended_next_phase") != next_phase:
        raise ValueError("Cannot resume schema v3 artifact with inconsistent recommended next phase")
    if data.get("active_blocker_categories") != active_categories:
        raise ValueError("Cannot resume schema v3 artifact with inconsistent active blocker categories")
    if data.get("evidence_logging_disabled") != telemetry.evidence_logging_disabled:
        raise ValueError("Cannot resume schema v3 artifact with inconsistent evidence logging flag")
    if data.get("telegram_sending_disabled") != telemetry.telegram_sending_disabled:
        raise ValueError("Cannot resume schema v3 artifact with inconsistent Telegram flag")
    if data.get("supabase_write_attempts") != telemetry.supabase_write_attempts:
        raise ValueError("Cannot resume schema v3 artifact with inconsistent Supabase write counter")
    if data.get("blocked_write_calls") != telemetry.blocked_write_attempts:
        raise ValueError("Cannot resume schema v3 artifact with inconsistent blocked write counter")
    if data.get("non_allowlisted_rpc_attempts") != telemetry.unknown_rpc_attempts:
        raise ValueError("Cannot resume schema v3 artifact with inconsistent unknown RPC counter")
    return AnswerQualityBaseline(
        schema_version=HARNESS_SCHEMA_VERSION,
        generated_at=str(data.get("generated_at") or ""),
        git_sha=str(data.get("git_sha") or ""),
        workspace=str(data.get("workspace") or ""),
        evidence_logging_disabled=telemetry.evidence_logging_disabled,
        telegram_sending_disabled=telemetry.telegram_sending_disabled,
        supabase_write_attempts=telemetry.supabase_write_attempts,
        blocked_write_calls=telemetry.blocked_write_attempts,
        non_allowlisted_rpc_attempts=telemetry.unknown_rpc_attempts,
        case_results=case_results,
        overall_classification=overall,
        primary_blocker=primary,
        recommended_next_phase=next_phase,
        active_blocker_categories=active_categories,
        production_safety_telemetry=telemetry,
        read_only_safety_state=dict(data.get("read_only_safety_state") or {}),
        run_state="completed",
    )


def _validate_v3_telemetry_contract(data: dict[str, Any]) -> ProductionSafetyTelemetry:
    if not isinstance(data.get("active_blocker_categories"), list):
        raise ValueError("Cannot resume schema v3 artifact without active blocker categories")
    telemetry = data.get("production_safety_telemetry")
    if not isinstance(telemetry, dict):
        raise ValueError("Cannot resume schema v3 artifact without production_safety_telemetry")
    required_fields = {
        "safety_result",
        *OPERATION_COUNTER_FIELDS,
        "allowlisted_rpc_calls_by_name",
        *RUNTIME_FLAG_FIELDS,
        "counter_provenance",
        "table_snapshots",
        *(f"{name}_changed" for name in SAFETY_SNAPSHOT_NAMES),
    }
    missing = sorted(required_fields - set(telemetry))
    table_snapshots = telemetry.get("table_snapshots")
    if missing or not isinstance(table_snapshots, dict):
        raise ValueError(
            "Cannot resume schema v3 artifact with incomplete production safety telemetry"
        )
    counters = {
        field_name: _require_non_negative_int(telemetry, field_name)
        for field_name in OPERATION_COUNTER_FIELDS
    }
    flags = {
        field_name: _require_bool(telemetry, field_name)
        for field_name in RUNTIME_FLAG_FIELDS
    }
    rpc_counts = _validate_rpc_counts(telemetry.get("allowlisted_rpc_calls_by_name"))
    if counters["allowlisted_rpc_calls_total"] != sum(rpc_counts.values()):
        raise ValueError("Cannot resume schema v3 artifact with inconsistent RPC counters")
    if flags["settings_loader_used"] and not flags["settings_loader_attempted"]:
        raise ValueError("Cannot resume schema v3 artifact with inconsistent settings loader flags")
    counter_provenance = _validate_counter_provenance(telemetry.get("counter_provenance"))
    missing_tables = sorted(set(SAFETY_SNAPSHOT_NAMES) - set(table_snapshots))
    if missing_tables:
        raise ValueError(
            "Cannot resume schema v3 artifact with incomplete production safety snapshots"
        )
    table_comparisons: dict[str, TableSafetyComparison] = {}
    for name in SAFETY_SNAPSHOT_NAMES:
        raw_comparison = table_snapshots.get(name)
        if not isinstance(raw_comparison, dict) or not {
            "before",
            "after",
            "comparison",
        }.issubset(raw_comparison):
            raise ValueError(
                "Cannot resume schema v3 artifact with malformed production safety snapshots"
            )
        before = _validate_table_snapshot_dict(raw_comparison.get("before"), f"{name}.before")
        after = _validate_table_snapshot_dict(raw_comparison.get("after"), f"{name}.after")
        computed = _compare_table_snapshots(before, after)
        saved_comparison = raw_comparison.get("comparison")
        if saved_comparison not in ("unchanged", "changed", "incomplete"):
            raise ValueError("Cannot resume schema v3 artifact with invalid snapshot comparison")
        if saved_comparison != computed.comparison:
            raise ValueError("Cannot resume schema v3 artifact with inconsistent snapshot comparison")
        saved_changed = telemetry.get(f"{name}_changed")
        computed_changed = _comparison_changed_value(computed.comparison)
        if saved_changed != computed_changed:
            raise ValueError("Cannot resume schema v3 artifact with inconsistent snapshot changed flag")
        table_comparisons[name] = computed
    computed_safety = _compute_safety_result(
        counters=counters,
        flags=flags,
        comparisons={name: comparison.comparison for name, comparison in table_comparisons.items()},
        counter_provenance=counter_provenance,
    )
    if telemetry.get("safety_result") != computed_safety:
        raise ValueError("Cannot resume schema v3 artifact with inconsistent production safety result")
    return ProductionSafetyTelemetry(
        safety_result=computed_safety,
        select_calls=counters["select_calls"],
        allowlisted_rpc_calls_total=counters["allowlisted_rpc_calls_total"],
        allowlisted_rpc_calls_by_name=rpc_counts,
        unknown_rpc_attempts=counters["unknown_rpc_attempts"],
        supabase_write_attempts=counters["supabase_write_attempts"],
        blocked_write_attempts=counters["blocked_write_attempts"],
        evidence_log_write_attempts=counters["evidence_log_write_attempts"],
        telegram_message_attempts=counters["telegram_message_attempts"],
        external_docs_operation_attempts=counters["external_docs_operation_attempts"],
        model_attempts=counters["model_attempts"],
        evidence_logging_disabled=flags["evidence_logging_disabled"],
        telegram_sending_disabled=flags["telegram_sending_disabled"],
        read_only_adapter_enabled=flags["read_only_adapter_enabled"],
        atomic_output_enabled=flags["atomic_output_enabled"],
        manual_env_access_performed=flags["manual_env_access_performed"],
        secret_values_rendered=flags["secret_values_rendered"],
        settings_loader_attempted=flags["settings_loader_attempted"],
        settings_loader_used=flags["settings_loader_used"],
        external_docs_operations_disabled=flags["external_docs_operations_disabled"],
        counter_provenance=counter_provenance,
        table_snapshots=table_comparisons,
        documents_changed=_comparison_changed_value(table_comparisons["documents"].comparison),
        document_versions_changed=_comparison_changed_value(table_comparisons["document_versions"].comparison),
        sections_changed=_comparison_changed_value(table_comparisons["sections"].comparison),
        chunks_changed=_comparison_changed_value(table_comparisons["chunks"].comparison),
        conversations_changed=_comparison_changed_value(table_comparisons["conversations"].comparison),
        messages_changed=_comparison_changed_value(table_comparisons["messages"].comparison),
        term_statistics_changed=_comparison_changed_value(table_comparisons["term_statistics"].comparison),
    )


def _require_non_negative_int(mapping: dict[str, Any], field_name: str) -> int:
    value = mapping.get(field_name)
    if type(value) is not int or value < 0:
        raise ValueError(f"Cannot resume schema v3 artifact with invalid counter: {field_name}")
    return value


def _require_bool(mapping: dict[str, Any], field_name: str) -> bool:
    value = mapping.get(field_name)
    if type(value) is not bool:
        raise ValueError(f"Cannot resume schema v3 artifact with invalid runtime flag: {field_name}")
    return value


def _validate_rpc_counts(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError("Cannot resume schema v3 artifact with invalid RPC counter map")
    rpc_counts: dict[str, int] = {}
    for raw_name, raw_count in value.items():
        if not isinstance(raw_name, str) or not raw_name:
            raise ValueError("Cannot resume schema v3 artifact with invalid RPC counter name")
        if raw_name not in READ_ONLY_RPC_ALLOWLIST:
            raise ValueError("Cannot resume schema v3 artifact with non-allowlisted RPC counter")
        if type(raw_count) is not int or raw_count < 0:
            raise ValueError("Cannot resume schema v3 artifact with invalid RPC counter value")
        rpc_counts[raw_name] = raw_count
    return dict(sorted(rpc_counts.items()))


def _validate_counter_provenance(value: object) -> dict[str, CounterProvenanceMode]:
    if not isinstance(value, dict) or set(value) != set(COUNTER_PROVENANCE_FIELDS):
        raise ValueError("Cannot resume schema v3 artifact with incomplete counter provenance")
    provenance: dict[str, CounterProvenanceMode] = {}
    for field_name in COUNTER_PROVENANCE_FIELDS:
        mode = value.get(field_name)
        if mode not in VALID_COUNTER_PROVENANCE_MODES:
            raise ValueError("Cannot resume schema v3 artifact with invalid counter provenance")
        provenance[field_name] = mode  # type: ignore[assignment]
    return provenance


def _validate_table_snapshot_dict(value: object, label: str) -> TableStateSnapshot:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"Cannot resume schema v3 artifact with malformed snapshot: {label}")
    status = value.get("snapshot_status")
    if status not in ("complete", "incomplete"):
        raise ValueError(f"Cannot resume schema v3 artifact with invalid snapshot status: {label}")
    source_relation = value.get("source_relation", "")
    if not isinstance(source_relation, str):
        raise ValueError(f"Cannot resume schema v3 artifact with invalid snapshot source: {label}")
    error_code = value.get("error_code", "")
    if error_code is None:
        error_code = ""
    if not isinstance(error_code, str):
        raise ValueError(f"Cannot resume schema v3 artifact with invalid snapshot error code: {label}")
    if _contains_secret_like(error_code) or ("http" in error_code.casefold() and "/" in error_code):
        raise ValueError(f"Cannot resume schema v3 artifact with unsafe snapshot error code: {label}")
    if status == "complete":
        row_count = value.get("row_count")
        digest = value.get("safe_metadata_digest")
        if type(row_count) is not int or row_count < 0:
            raise ValueError(f"Cannot resume schema v3 artifact with invalid complete snapshot count: {label}")
        if not isinstance(digest, str) or not SNAPSHOT_DIGEST_RE.fullmatch(digest):
            raise ValueError(f"Cannot resume schema v3 artifact with invalid complete snapshot digest: {label}")
        if error_code:
            raise ValueError(f"Cannot resume schema v3 artifact with complete snapshot error code: {label}")
        return TableStateSnapshot(
            row_count=row_count,
            safe_metadata_digest=digest,
            snapshot_status="complete",
            source_relation=source_relation,
        )
    error_code = error_code.strip()
    if not error_code or not SAFE_ERROR_CODE_RE.fullmatch(error_code):
        raise ValueError(f"Cannot resume schema v3 artifact with unsafe incomplete snapshot error code: {label}")
    return TableStateSnapshot(
        row_count=None,
        safe_metadata_digest="",
        snapshot_status="incomplete",
        error_code=error_code,
        source_relation=source_relation,
    )


def _validate_v3_case_results(data: dict[str, Any]) -> list[AnswerQualityCaseResult]:
    rows = data.get("case_results")
    if not isinstance(rows, list):
        raise ValueError("Cannot resume schema v3 artifact without case results")
    fixed_cases = {case.case_id: case for case in fixed_answer_quality_cases()}
    required_ids = set(fixed_cases) | set(ANSWER_QUALITY_DYNAMIC_CASE_IDS)
    seen: set[str] = set()
    results: list[AnswerQualityCaseResult] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Cannot resume schema v3 artifact with malformed case result")
        case_id = str(row.get("case_id") or "")
        if not case_id:
            raise ValueError("Cannot resume schema v3 artifact with empty case id")
        if case_id in seen:
            raise ValueError("Cannot resume schema v3 artifact with duplicate case id")
        seen.add(case_id)
        if case_id not in required_ids:
            raise ValueError("Cannot resume schema v3 artifact with unknown case id")
        case = _resume_case_contract(row, fixed_cases)
        results.append(_validate_v3_case_result(row, case))
    missing = sorted(required_ids - seen)
    if missing:
        raise ValueError("Cannot resume completed schema v3 artifact with missing required case")
    return results


def _resume_case_contract(
    row: dict[str, Any],
    fixed_cases: dict[str, AnswerQualityCase],
) -> AnswerQualityCase:
    case_id = str(row.get("case_id") or "")
    if case_id in fixed_cases:
        case = fixed_cases[case_id]
        if str(row.get("question") or "") != _sanitize_text(case.question, max_chars=500):
            raise ValueError("Cannot resume schema v3 artifact with mismatched fixed case question")
        return case
    outcome = row.get("outcome")
    if outcome == "BLOCKED":
        return AnswerQualityCase(
            case_id=case_id,
            question=str(row.get("question") or ""),
            case_type="blocked_resume_fixture",
            notes="Resume artifact records a blocked dynamic fixture.",
        )
    if outcome == "SKIPPED":
        return AnswerQualityCase(
            case_id=case_id,
            question=str(row.get("question") or ""),
            case_type="skipped_resume_fixture",
            blocking_for_current_phase=False,
            notes="Resume artifact records a skipped dynamic fixture.",
        )
    if case_id == "uploaded_material_only_auto":
        return AnswerQualityCase(
            case_id=case_id,
            question=str(row.get("question") or ""),
            case_type="uploaded_material_only",
            expected_source_types=(SOURCE_CLASS_UPLOADED,),
            requires_accepted_evidence=True,
        )
    if case_id == "mixed_course_service_auto":
        expected_services = tuple(
            _normalize_service_id(value)
            for value in _string_list(
                (row.get("detected_service_metadata") or {}).get("expected_service_ids")
                if isinstance(row.get("detected_service_metadata"), dict)
                else []
            )
            if value
        )
        if not expected_services:
            raise ValueError("Cannot resume mixed-source case without expected service diagnostics")
        return AnswerQualityCase(
            case_id=case_id,
            question=str(row.get("question") or ""),
            case_type="mixed_course_service",
            expected_service_ids=expected_services,
            service_expectation="required",
            expected_source_types=(SOURCE_CLASS_UPLOADED, SOURCE_CLASS_EXTERNAL),
            requires_accepted_evidence=True,
            requires_mixed_sources=True,
        )
    if case_id == "archived_exclusion":
        return AnswerQualityCase(
            case_id=case_id,
            question=str(row.get("question") or ""),
            case_type="archived_exclusion",
            check_archived_exclusion=True,
        )
    raise ValueError("Cannot resume schema v3 artifact with unsupported dynamic case")


def _validate_v3_case_result(
    row: dict[str, Any],
    case: AnswerQualityCase,
) -> AnswerQualityCaseResult:
    if case.case_type.startswith("blocked_") or case.case_type.startswith("skipped_"):
        return _validate_fixture_case_result(row, case)
    required_fields = {
        "outcome",
        "pipeline_status",
        "checks",
        "expectation_diagnostics",
        "failure_codes",
        "blocker_categories",
        "blocking_for_current_phase",
    }
    if not required_fields.issubset(row):
        raise ValueError("Cannot resume schema v3 artifact with incomplete case classification")
    checks = _validate_case_checks(row.get("checks"))
    diagnostics = _validate_case_diagnostics(row.get("expectation_diagnostics"))
    warnings, failures, failure_codes = _case_warnings_and_failures_from_status(
        case,
        checks,
        diagnostics,
        str(row.get("pipeline_status") or ""),
    )
    outcome = _case_outcome(case, warnings, failures)
    blocker_categories = _blocker_categories_for_case(
        case,
        outcome=outcome,
        failure_codes=failure_codes,
    )
    blocking = case.blocking_for_current_phase
    if row.get("outcome") != outcome:
        raise ValueError("Cannot resume schema v3 artifact with inconsistent case verdict")
    if row.get("failure_codes") != failure_codes:
        raise ValueError("Cannot resume schema v3 artifact with inconsistent failure codes")
    if row.get("blocker_categories") != blocker_categories:
        raise ValueError("Cannot resume schema v3 artifact with inconsistent blocker categories")
    if row.get("blocking_for_current_phase") != blocking:
        raise ValueError("Cannot resume schema v3 artifact with inconsistent phase blocker flag")
    result = _case_result_from_dict(row)
    result.outcome = outcome
    result.warnings = warnings
    result.failures = failures
    result.failure_codes = failure_codes
    result.blocker_categories = blocker_categories
    result.blocking_for_current_phase = blocking
    result.checks = checks
    result.expectation_diagnostics = diagnostics
    return result


def _validate_fixture_case_result(
    row: dict[str, Any],
    case: AnswerQualityCase,
) -> AnswerQualityCaseResult:
    outcome = _case_outcome(case, warnings=[], failures=[])
    failure_codes: list[FailureCode] = ["fixture_missing"] if outcome == "BLOCKED" else []
    blocker_categories: list[BlockerCategory] = []
    blocking = case.blocking_for_current_phase
    if row.get("outcome") != outcome:
        raise ValueError("Cannot resume schema v3 artifact with inconsistent fixture verdict")
    if row.get("failure_codes", []) != failure_codes:
        raise ValueError("Cannot resume schema v3 artifact with inconsistent fixture failure codes")
    if row.get("blocker_categories", []) != blocker_categories:
        raise ValueError("Cannot resume schema v3 artifact with inconsistent fixture blocker categories")
    if row.get("blocking_for_current_phase", blocking) != blocking:
        raise ValueError("Cannot resume schema v3 artifact with inconsistent fixture phase blocker flag")
    result = _case_result_from_dict(row)
    result.outcome = outcome
    result.failure_codes = failure_codes
    result.blocker_categories = blocker_categories
    result.blocking_for_current_phase = blocking
    return result


def _validate_case_checks(value: object) -> CaseChecks:
    if not isinstance(value, dict):
        raise ValueError("Cannot resume schema v3 artifact with malformed case checks")
    missing = sorted(set(CaseChecks.__dataclass_fields__) - set(value))
    if missing:
        raise ValueError("Cannot resume schema v3 artifact with incomplete case checks")
    checked: dict[str, bool] = {}
    for field_name in CaseChecks.__dataclass_fields__:
        raw_value = value.get(field_name)
        if type(raw_value) is not bool:
            raise ValueError("Cannot resume schema v3 artifact with non-boolean case check")
        checked[field_name] = raw_value
    return CaseChecks(**checked)


def _validate_case_diagnostics(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Cannot resume schema v3 artifact with malformed case diagnostics")
    required_fields = {
        "answer_mode",
        "evidence_required",
        "expected_service_ids",
        "selected_service_ids",
        "accepted_evidence_service_ids",
        "final_source_service_ids",
        "unexpected_service_ids",
        "accepted_evidence_source_classes",
        "final_source_classes",
        "required_evidence_term_groups",
        "optional_evidence_term_groups",
    }
    if not required_fields.issubset(value):
        raise ValueError("Cannot resume schema v3 artifact with incomplete case diagnostics")
    if type(value.get("evidence_required")) is not bool:
        raise ValueError("Cannot resume schema v3 artifact with invalid case diagnostics")
    for field_name in (
        "expected_service_ids",
        "selected_service_ids",
        "accepted_evidence_service_ids",
        "final_source_service_ids",
        "unexpected_service_ids",
        "accepted_evidence_source_classes",
        "final_source_classes",
    ):
        if not _is_string_list(value.get(field_name)):
            raise ValueError("Cannot resume schema v3 artifact with invalid case diagnostic list")
    for field_name in ("required_evidence_term_groups", "optional_evidence_term_groups"):
        if not _is_evidence_group_diagnostics(value.get(field_name)):
            raise ValueError("Cannot resume schema v3 artifact with invalid evidence group diagnostics")
    return dict(value)


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_evidence_group_diagnostics(value: object) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, dict):
            return False
        if type(item.get("satisfied")) is not bool:
            return False
        if not _is_string_list(item.get("alternatives")) or not _is_string_list(item.get("matched_terms")):
            return False
    return True


def _normalize_expectation_text(text: str) -> str:
    clean = str(text or "").casefold().replace("ё", "е")
    clean = _normalize_expectation_dashes(clean)
    return re.sub(r"\s+", " ", clean).strip()


def _normalize_expectation_dashes(text: str) -> str:
    normalized: list[str] = []
    in_dash_run = False
    for char in text:
        if _expectation_dash_char(char):
            if not in_dash_run:
                normalized.append("-")
            in_dash_run = True
            continue
        normalized.append(char)
        in_dash_run = False
    return "".join(normalized)


def _expectation_term_matches(term: str, text: str) -> bool:
    normalized_term = _normalize_expectation_text(term)
    normalized_text = _normalize_expectation_text(text)
    if not normalized_term or not normalized_text:
        return False

    term_pattern = re.escape(normalized_term).replace(r"\ ", r"\s+")
    for match in re.finditer(term_pattern, normalized_text):
        if _has_expectation_boundaries(normalized_text, match.start(), match.end()):
            return True
    return False


def _has_expectation_boundaries(text: str, start: int, end: int) -> bool:
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    if _expectation_identifier_char(before) or _expectation_identifier_char(after):
        return False
    if _expectation_dash_char(before) and start > 1 and _expectation_identifier_char(text[start - 2]):
        return False
    if _expectation_dash_char(after) and end + 1 < len(text) and _expectation_identifier_char(text[end + 1]):
        return False
    return True


def _expectation_dash_char(char: str) -> bool:
    return bool(char) and (char == "\u2212" or unicodedata.category(char) == "Pd")


def _expectation_identifier_char(char: str) -> bool:
    return bool(char) and (char.isalnum() or char == "_")


def _insufficient_evidence_handled(case: AnswerQualityCase, result: "PipelineResult") -> bool:
    status = str(getattr(result.status, "value", result.status))
    answer = (result.answer or "").lower()
    if not case.expected_limitation:
        return True
    if status in {"insufficient_evidence", "needs_clarification"}:
        return True
    return any(term in answer for term in ("не хватает", "недостаточно", "уточните", "не могу определить", "нет данных"))


def _contains_secret_like(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in SECRET_PATTERNS)


def _contains_residue(text: str) -> bool:
    lowered = (text or "").lower()
    return any(signal.lower() in lowered for signal in RESIDUE_SIGNALS)


def _residue_signals(text: str) -> list[str]:
    lowered = (text or "").lower()
    return [signal for signal in RESIDUE_SIGNALS if signal.lower() in lowered]


def _source_class(source_type: str) -> str:
    if source_type == SOURCE_TYPE_EXTERNAL:
        return SOURCE_CLASS_EXTERNAL
    if source_type:
        return SOURCE_CLASS_UPLOADED
    return SOURCE_CLASS_UNKNOWN


def _metadata_source_name(metadata: dict[str, Any]) -> str:
    for key in ("source_name", "service", "service_id", "source_id"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    source = metadata.get("source")
    if isinstance(source, dict):
        for key in ("name", "id", "service"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _metadata_service_ids(metadata: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("service_id", "service"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value)
    service_ids = metadata.get("service_ids")
    if isinstance(service_ids, list):
        values.extend(str(value) for value in service_ids if str(value).strip())
    mentions = metadata.get("service_mentions")
    if isinstance(mentions, list):
        values.extend(
            str(item.get("service_id"))
            for item in mentions
            if isinstance(item, dict) and str(item.get("service_id") or "").strip()
        )
    if not values:
        source_name = _metadata_source_name(metadata)
        if source_name:
            values.append(source_name)
    return tuple(_dedupe_strings(_normalize_service_id(value) for value in values))


def _normalize_service_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")
    for suffix in ("_documentation", "_docs"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].rstrip("_")
            break
    return normalized


def _service_ids_for_document_ids(
    document_ids: list[str] | set[str],
    hydration: dict[str, DocumentSummary],
) -> set[str]:
    return {
        service_id
        for document_id in document_ids
        for service_id in hydration.get(document_id, DocumentSummary("", "", "", "", "")).service_ids
        if service_id
    }


def _source_classes_for_document_ids(
    document_ids: list[str] | set[str],
    hydration: dict[str, DocumentSummary],
) -> set[str]:
    return {
        doc.source_class
        for document_id in document_ids
        if (doc := hydration.get(document_id)) is not None and doc.source_class
    }


def _dedupe_strings(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item) for item in value if str(item)]


def _first_safe_card_question(cards: list[dict[str, Any]]) -> str:
    for card in cards:
        questions = card.get("questions_answered")
        if not isinstance(questions, list):
            continue
        for question in questions:
            text = str(question or "").strip()
            if _safe_dynamic_question(text):
                return text
    return ""


def _safe_dynamic_question(text: str) -> bool:
    if not text or len(text) > 220:
        return False
    if _contains_secret_like(text) or UUID_RE.search(text):
        return False
    lowered = text.lower()
    sensitive = ("пароль", "token", "secret", "карта", "passport", "паспорт", "телефон")
    return not any(term in lowered for term in sensitive)


def _card_terms(cards: list[dict[str, Any]]) -> list[str]:
    terms: list[str] = []
    for card in cards:
        for key in ("questions_answered", "topics", "entities"):
            value = card.get(key)
            if isinstance(value, list):
                terms.extend(str(item) for item in value)
        if card.get("summary"):
            terms.append(str(card["summary"]))
    return terms


def _sanitize_json(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _sanitize_json(val) for key, val in value.items() if not _sensitive_key(str(key))}
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value, max_chars=1000)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _sanitize_text(str(value), max_chars=1000)


def _sanitize_text(text: str, *, max_chars: int) -> str:
    clean = text.replace("\r\n", "\n").replace("\r", "\n")
    clean = UUID_RE.sub("<uuid-redacted>", clean)
    for pattern in SECRET_PATTERNS:
        clean = pattern.sub("<secret-redacted>", clean)
    if len(clean) > max_chars:
        clean = clean[: max_chars - 1].rstrip() + "…"
    return clean


def _sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in {"secret_values_rendered", "no_secret_like_text"}:
        return False
    return any(token in lowered for token in ("authorization", "apikey", "service_role", "secret", "token", "private_key"))


def _hash_identifier(value: str) -> str:
    if not value:
        return ""
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _safe_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _case_result_from_dict(row: dict[str, Any]) -> AnswerQualityCaseResult:
    selected_docs = [
        DocumentSummary(
            **{
                **doc,
                "service_ids": tuple(doc.get("service_ids") or ()),
            }
        )
        for doc in row.get("selected_documents", [])
        if isinstance(doc, dict)
        and {"document_id_hash", "title", "source_type", "source_class", "status"}.issubset(doc)
    ]
    checks_data = row.get("checks") if isinstance(row.get("checks"), dict) else {}
    return AnswerQualityCaseResult(
        case_id=str(row.get("case_id") or ""),
        question=str(row.get("question") or ""),
        outcome=str(row.get("outcome") or "FAIL"),  # type: ignore[arg-type]
        pipeline_status=str(row.get("pipeline_status") or ""),
        final_answer=str(row.get("final_answer") or ""),
        selected_documents=selected_docs,
        selected_source_classes=list(row.get("selected_source_classes") or []),
        selected_services=list(row.get("selected_services") or []),
        detected_service_metadata=dict(row.get("detected_service_metadata") or {}),
        accepted_evidence_summary=list(row.get("accepted_evidence_summary") or []),
        final_sources=list(row.get("final_sources") or []),
        checks=CaseChecks(**{key: bool(value) for key, value in checks_data.items() if key in CaseChecks.__dataclass_fields__}),
        expectation_diagnostics=dict(row.get("expectation_diagnostics") or {}),
        manual_review=dict(row.get("manual_review") or {}),
        warnings=list(row.get("warnings") or []),
        failures=list(row.get("failures") or []),
        failure_codes=list(row.get("failure_codes") or []),  # type: ignore[arg-type]
        blocker_categories=list(row.get("blocker_categories") or []),  # type: ignore[arg-type]
        blocking_for_current_phase=bool(row.get("blocking_for_current_phase", True)),
        duration_seconds=float(row.get("duration_seconds") or 0.0),
        model_metadata=dict(row.get("model_metadata") or {}),
        read_only_safety_state=dict(row.get("read_only_safety_state") or {}),
    )


def run_async(coro: object) -> object:
    """Run a coroutine from synchronous CLI code."""
    return asyncio.run(coro)  # type: ignore[arg-type]
