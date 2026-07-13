"""Safe answer-quality harness for no-write RAG baseline audits."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from app.db.supabase_client import SupabaseClient
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


HARNESS_SCHEMA_VERSION = "phase7c-a-answer-quality-baseline-v1"
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
    "mixed_source_allocation_gap",
    "evidence_selection_gap",
    "answer_generation_gap",
    "citation_source_label_gap",
    "insufficient_evidence_handling_gap",
    "documentation_residue_affects_answers",
    "no_blocking_functional_gap",
]


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
        return {
            "allowed_selects": self.allowed_selects,
            "allowed_rpc_calls": self.allowed_rpc_calls,
            "allowed_rpc_names": sorted(set(self.allowed_rpc_names)),
            "rpc_allowlist": sorted(READ_ONLY_RPC_ALLOWLIST),
            "write_attempts": self.write_attempts,
            "blocked_write_calls": self.blocked_write_calls,
            "non_allowlisted_rpc_attempts": self.non_allowlisted_rpc_attempts,
            "blocked_operations": list(self.blocked_operations),
        }


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
    expected_source_types: tuple[str, ...] = ()
    expected_terms: tuple[str, ...] = ()
    allowed_statuses: tuple[str, ...] = ("answered", "insufficient_evidence", "needs_clarification")
    requires_mixed_sources: bool = False
    check_archived_exclusion: bool = True
    expected_limitation: str = ""
    notes: str = ""


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


@dataclass
class CaseChecks:
    """Automated checks for one case."""

    answer_non_empty: bool = False
    status_allowed: bool = False
    selected_documents_active: bool = False
    accepted_evidence_active: bool = False
    sources_from_accepted_evidence: bool = False
    archived_absent: bool = False
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
    manual_review: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
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
    read_only_safety_state: dict[str, object] = field(default_factory=dict)


@dataclass
class AnswerQualityRuntimeResources:
    """Long-lived resources owned by the answer-quality runtime."""

    supabase: ReadOnlySupabaseClient
    openrouter_client: OpenRouterClient
    embedding_client: OllamaEmbeddingClient

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
            expected_service_ids=("telegram",),
            expected_source_types=(SOURCE_CLASS_EXTERNAL,),
            expected_terms=("sendMessage", "chat_id", "text"),
        ),
        AnswerQualityCase(
            case_id="n8n_docs",
            question="как отправить HTTP-запрос из n8n?",
            case_type="official_or_course_docs",
            expected_service_ids=("n8n",),
            expected_terms=("HTTP Request", "method"),
        ),
        AnswerQualityCase(
            case_id="openrouter_docs",
            question="как подключить OpenRouter API ключ?",
            case_type="official_docs",
            expected_service_ids=("openrouter",),
            expected_source_types=(SOURCE_CLASS_EXTERNAL,),
            expected_terms=("API key", "Authorization", "Bearer"),
        ),
        AnswerQualityCase(
            case_id="supabase_docs",
            question="как сделать векторный поиск по документам в Supabase?",
            case_type="official_docs",
            expected_service_ids=("supabase",),
            expected_terms=("pgvector", "embeddings", "match_documents"),
        ),
        AnswerQualityCase(
            case_id="ambiguous_service",
            question="как подключить API и настроить запрос?",
            case_type="ambiguous_service",
            allowed_statuses=("insufficient_evidence", "needs_clarification", "answered"),
            expected_limitation="service_ambiguous",
        ),
        AnswerQualityCase(
            case_id="out_of_base",
            question="какой у меня сейчас баланс на банковской карте?",
            case_type="out_of_base",
            allowed_statuses=("insufficient_evidence", "needs_clarification"),
            expected_limitation="out_of_base",
        ),
        AnswerQualityCase(
            case_id="followup_without_memory",
            question="а какой параметр нужно указать для этого?",
            case_type="followup_without_memory",
            allowed_statuses=("insufficient_evidence", "needs_clarification"),
            expected_limitation="conversation_memory_not_implemented",
        ),
    ]


def build_answer_quality_runtime_from_settings(settings: "Settings", *, answer_mode: str = "cheap") -> AnswerQualityRuntime:
    """Build a separate no-write RAG runtime without Telegram dependencies."""
    if not settings.default_workspace_id:
        raise RuntimeError("DEFAULT_WORKSPACE_ID is required for the answer-quality harness")

    supabase = ReadOnlySupabaseClient(SupabaseClient(settings))
    openrouter_client = OpenRouterClient(settings)
    embedding_client = OllamaEmbeddingClient(settings)
    model_router = ModelRouter(openrouter_client, ModelRouterConfig.from_settings(settings))
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
    services = sorted({doc.source_name for doc in selected_documents if doc.source_name})
    source_labels = SourceLabelBuilder().build_many(result.sources)
    final_answer = _sanitize_text(result.answer, max_chars=6000)
    evidence_summary = [_evidence_summary(item, hydration) for item in accepted]
    checks = _case_checks(case, result, hydration, selected_ids, accepted, source_labels)
    warnings, failures = _case_warnings_and_failures(case, checks, source_classes, services, evidence_summary, result)
    outcome = _case_outcome(case, warnings, failures)
    return AnswerQualityCaseResult(
        case_id=case.case_id,
        question=_sanitize_text(case.question, max_chars=500),
        outcome=outcome,
        pipeline_status=str(getattr(result.status, "value", result.status)),
        final_answer=final_answer,
        selected_documents=selected_documents,
        selected_source_classes=source_classes,
        selected_services=services,
        detected_service_metadata=_sanitize_json(
            {
                "expected_service_ids": list(case.expected_service_ids),
                "query_plan_domain_hint": (debug.get("query_plan") or {}).get("domain_hint")
                if isinstance(debug.get("query_plan"), dict)
                else "",
                "course_hint": debug.get("course_hint", ""),
            }
        ),
        accepted_evidence_summary=evidence_summary,
        final_sources=[_sanitize_text(label, max_chars=300) for label in source_labels],
        checks=checks,
        manual_review=_manual_review_stub(case, checks, source_classes),
        warnings=warnings,
        failures=failures,
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

    blocking = [case for case in case_results if case.outcome in {"FAIL", "WARN"} and case.case_id != "followup_without_memory"]
    environment_blocked = [case for case in case_results if case.outcome == "BLOCKED"]
    if blocking:
        primary = _select_primary_blocker(blocking)
        return "functional_blocker_found", primary, f"Phase 7C-B - one focused fix for {primary}"
    if environment_blocked:
        return "incomplete_environment", "no_blocking_functional_gap", "Phase 7C-A remains active"
    return "baseline_pass", "no_blocking_functional_gap", "Phase 8A - Uploaded File Lifecycle and Storage Hygiene"


def baseline_from_results(
    *,
    case_results: list[AnswerQualityCaseResult],
    git_sha: str,
    workspace_id: str,
    safety_state: dict[str, object],
) -> AnswerQualityBaseline:
    """Build the top-level baseline artifact."""
    overall, primary, next_phase = classify_baseline(case_results)
    return AnswerQualityBaseline(
        schema_version=HARNESS_SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        git_sha=git_sha,
        workspace=_hash_identifier(workspace_id),
        evidence_logging_disabled=True,
        telegram_sending_disabled=True,
        supabase_write_attempts=int(safety_state.get("write_attempts", 0)),
        blocked_write_calls=int(safety_state.get("blocked_write_calls", 0)),
        non_allowlisted_rpc_attempts=int(safety_state.get("non_allowlisted_rpc_attempts", 0)),
        case_results=case_results,
        overall_classification=overall,
        primary_blocker=primary,
        recommended_next_phase=next_phase,
        read_only_safety_state=safety_state,
    )


def save_baseline_atomic(path: Path, baseline: AnswerQualityBaseline) -> None:
    """Write a UTF-8 JSON baseline artifact atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dataclass_to_sanitized_dict(baseline)
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


def load_existing_case_results(path: Path) -> list[AnswerQualityCaseResult]:
    """Load previously completed case results for resume mode."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("case_results", []) if isinstance(data, dict) else []
    results: list[AnswerQualityCaseResult] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        results.append(_case_result_from_dict(row))
    return results


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
) -> CaseChecks:
    answer = result.answer or ""
    status = str(getattr(result.status, "value", result.status))
    selected_active = bool(selected_ids) and all(hydration.get(doc_id, DocumentSummary("", "", "", "", "")).status == "active" for doc_id in selected_ids)
    accepted_doc_ids = {str(item.get("document_id") or "") for item in accepted if item.get("document_id")}
    accepted_active = bool(accepted) and all(hydration.get(doc_id, DocumentSummary("", "", "", "", "")).status == "active" for doc_id in accepted_doc_ids)
    source_evidence_ids = {str(source.evidence_id) for source in result.sources if source.evidence_id}
    accepted_evidence_ids = {str(item.get("evidence_id") or "") for item in accepted}
    answer_lower = answer.lower()
    expected_terms_present = True
    if case.expected_terms:
        expected_terms_present = any(term.lower() in answer_lower or _term_in_evidence(term, accepted) for term in case.expected_terms)
    residue_absent = not _contains_residue(answer)
    return CaseChecks(
        answer_non_empty=bool(answer.strip()),
        status_allowed=status in case.allowed_statuses,
        selected_documents_active=selected_active or not selected_ids,
        accepted_evidence_active=accepted_active or not accepted,
        sources_from_accepted_evidence=source_evidence_ids.issubset(accepted_evidence_ids),
        archived_absent=all(doc.status != "archived" for doc in hydration.values()),
        no_raw_uuid_in_answer=UUID_RE.search(answer) is None,
        no_debug_metadata_in_answer=not any(token in answer for token in ("chunk_id", "document_id", "evidence_id", "{'debug'", '"debug"', "metadata")),
        no_evidence_block_leak="Evidence:" not in answer and "Accepted evidence" not in answer,
        no_secret_like_text=not _contains_secret_like(answer + "\n" + "\n".join(source_labels)),
        no_residue_in_answer=residue_absent,
        expected_terms_present=expected_terms_present,
        insufficient_evidence_handled=_insufficient_evidence_handled(case, result),
    )


def _case_warnings_and_failures(
    case: AnswerQualityCase,
    checks: CaseChecks,
    source_classes: list[str],
    services: list[str],
    evidence_summary: list[dict[str, object]],
    result: "PipelineResult",
) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    failures: list[str] = []
    if not checks.answer_non_empty:
        failures.append("final answer is empty")
    if not checks.status_allowed:
        failures.append("pipeline status is outside the case expectation")
    if not checks.selected_documents_active:
        failures.append("selected document metadata did not confirm active status")
    if not checks.accepted_evidence_active:
        failures.append("accepted evidence did not hydrate to active documents")
    if not checks.sources_from_accepted_evidence:
        failures.append("final sources are not limited to accepted evidence")
    if case.check_archived_exclusion and not checks.archived_absent:
        failures.append("archived evidence appeared in selected or accepted evidence")
    if not checks.no_raw_uuid_in_answer or not checks.no_debug_metadata_in_answer or not checks.no_evidence_block_leak:
        failures.append("internal identifiers or debug metadata leaked into the answer")
    if not checks.no_secret_like_text:
        failures.append("secret-like token appeared in the sanitized report")
    if not checks.no_residue_in_answer:
        failures.append("dirty documentation residue entered the final answer")
    if case.expected_source_types and not set(case.expected_source_types).issubset(set(source_classes)):
        failures.append("expected source class was not selected")
    if case.requires_mixed_sources and not {SOURCE_CLASS_UPLOADED, SOURCE_CLASS_EXTERNAL}.issubset(set(source_classes)):
        failures.append("mixed uploaded/course and external documentation evidence was not selected")
    if case.expected_terms and not checks.expected_terms_present:
        warnings.append("expected high-signal term was not found in answer or accepted evidence")
    if case.expected_service_ids and services:
        lowered_services = " ".join(services).lower()
        if not any(service.lower() in lowered_services for service in case.expected_service_ids):
            warnings.append("selected service metadata did not match the expected service")
    if case.case_type == "ambiguous_service" and str(getattr(result.status, "value", result.status)) == "answered":
        if source_classes or evidence_summary:
            warnings.append("ambiguous service question selected evidence instead of asking for clarification")
    if case.case_type == "out_of_base" and str(getattr(result.status, "value", result.status)) == "answered":
        failures.append("out-of-base question received a confident answered status")
    if case.expected_limitation and not checks.insufficient_evidence_handled:
        warnings.append("expected limitation was not clearly handled")
    return warnings, failures


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
        "correct_service_found": bool(case.expected_service_ids),
        "required_source_classes_present": not case.requires_mixed_sources
        or {SOURCE_CLASS_UPLOADED, SOURCE_CLASS_EXTERNAL}.issubset(set(source_classes)),
        "answer_practical_and_understandable": checks.answer_non_empty,
        "sources_readable": True,
        "internal_data_leaked": not checks.no_raw_uuid_in_answer or not checks.no_debug_metadata_in_answer,
        "dirty_residue_affected_answer": not checks.no_residue_in_answer,
        "insufficient_evidence_handled": checks.insufficient_evidence_handled,
    }


def _select_primary_blocker(cases: list[AnswerQualityCaseResult]) -> PrimaryBlocker:
    if any("unsupported" in " ".join(case.failures + case.warnings).lower() for case in cases):
        return "answer_generation_gap"
    if any("expected source class" in " ".join(case.failures).lower() for case in cases):
        return "explicit_service_routing_gap"
    if any("mixed uploaded" in " ".join(case.failures).lower() for case in cases):
        return "mixed_source_allocation_gap"
    if any("selected document" in " ".join(case.failures).lower() or "accepted evidence" in " ".join(case.failures).lower() for case in cases):
        return "evidence_selection_gap"
    if any("internal identifiers" in " ".join(case.failures).lower() or "sources" in " ".join(case.failures).lower() for case in cases):
        return "citation_source_label_gap"
    if any("dirty documentation residue" in " ".join(case.failures).lower() for case in cases):
        return "documentation_residue_affects_answers"
    if any("out-of-base" in " ".join(case.failures).lower() or "expected limitation" in " ".join(case.warnings).lower() for case in cases):
        return "insufficient_evidence_handling_gap"
    return "evidence_selection_gap"


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
                    expected_service_ids=(service,),
                    expected_source_types=(SOURCE_CLASS_UPLOADED, SOURCE_CLASS_EXTERNAL),
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
        manual_review={"blocked_reason": case.case_type},
    )


def _skipped_result(case: AnswerQualityCase) -> AnswerQualityCaseResult:
    return AnswerQualityCaseResult(
        case_id=case.case_id,
        question=case.question,
        outcome="SKIPPED",
        warnings=[case.notes or case.case_type],
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
            "locator": item.get("locator"),
            "score": item.get("score"),
            "preview": _sanitize_text(text, max_chars=300),
            "residue_signals": _residue_signals(text),
        }
    )


def _term_in_evidence(term: str, accepted: list[dict[str, Any]]) -> bool:
    term_lower = term.lower()
    return any(term_lower in str(item.get("text") or "").lower() for item in accepted)


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
        DocumentSummary(**doc)
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
        manual_review=dict(row.get("manual_review") or {}),
        warnings=list(row.get("warnings") or []),
        failures=list(row.get("failures") or []),
        duration_seconds=float(row.get("duration_seconds") or 0.0),
        model_metadata=dict(row.get("model_metadata") or {}),
        read_only_safety_state=dict(row.get("read_only_safety_state") or {}),
    )


def run_async(coro: object) -> object:
    """Run a coroutine from synchronous CLI code."""
    return asyncio.run(coro)  # type: ignore[arg-type]
