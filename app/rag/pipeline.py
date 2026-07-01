"""Evidence-first RAG pipeline orchestration."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Protocol

from app.rag.answer_generator import AnswerGenerator
from app.rag.claim_verifier import ClaimVerifier
from app.rag.document_router import DocumentRouter
from app.rag.evidence_pack import EvidencePackBuilder, build_sources
from app.rag.evidence_retriever import EvidenceRetriever
from app.rag.question_analysis import QuestionAnalyzer
from app.rag.reranker import EvidenceReranker
from app.rag.source_labels import SourceLabelBuilder
from app.rag.types import AnswerStatus, DocumentCandidate, EvidencePack, PipelineResult

LOGGER = logging.getLogger(__name__)


class EvidenceLogger(Protocol):
    """Optional evidence log writer."""

    async def log_evidence(
        self,
        *,
        workspace_id: str,
        question: str,
        question_analysis: dict[str, object],
        document_candidates: list[dict[str, object]],
        evidence_pack: dict[str, object],
        final_answer: str,
        final_sources: list[str],
    ) -> None:
        """Persist one evidence-first pipeline trace."""


class EvidenceFirstRagPipeline:
    """Coordinates the evidence-first RAG flow."""

    def __init__(
        self,
        analyzer: QuestionAnalyzer,
        router: DocumentRouter,
        retriever: EvidenceRetriever,
        reranker: EvidenceReranker,
        pack_builder: EvidencePackBuilder,
        answer_generator: AnswerGenerator,
        verifier: ClaimVerifier,
        logger: EvidenceLogger | None = None,
    ) -> None:
        self._analyzer = analyzer
        self._router = router
        self._retriever = retriever
        self._reranker = reranker
        self._pack_builder = pack_builder
        self._answer_generator = answer_generator
        self._verifier = verifier
        self._logger = logger

    async def answer(
        self,
        question: str,
        *,
        workspace_id: str = "",
        course: str | None = None,
        dialog_context: object | None = None,
    ) -> PipelineResult:
        """Run the evidence-first pipeline for one question."""
        analysis = self._analyzer.analyze(question)
        if not analysis.source_required:
            documents: tuple[DocumentCandidate, ...] = ()
            evidence = EvidencePack(answer_mode="general_answer_without_sources")
        else:
            documents = await self._route_documents(analysis, workspace_id=workspace_id, course=course)
            spans = await self._retriever.retrieve(analysis, documents)
            reranked = self._rerank(spans, analysis)
            evidence = self._pack_builder.build(reranked, analysis=analysis)
        draft = await self._answer_generator.generate(analysis, evidence, dialog_context=dialog_context)
        verification = self._verifier.verify(draft, evidence)
        generation_debug = _generation_debug(draft)
        if verification.source_leakage and verification.verdict in {"rewrite", "fail"}:
            generation_debug = generation_debug | {
                "fallback_used": True,
                "weak_llm_answer_reason": generation_debug.get("weak_llm_answer_reason") or "source_label_only",
            }

        final_answer = verification.safe_answer if verification.verdict in {"rewrite", "fail"} else draft.text
        status = _final_status(draft.status, verification.verdict)
        source_strings = build_sources(evidence) if _can_show_sources(evidence, verification.verdict) else []
        if source_strings:
            final_answer = _append_sources(final_answer, source_strings)
        source_refs = evidence.sources() if source_strings else ()

        await self._log(
            workspace_id=workspace_id,
            question=question,
            documents=documents,
            evidence=evidence,
            final_answer=final_answer,
            final_sources=source_strings,
            question_analysis=asdict(analysis),
            generation_debug=generation_debug,
            source_label_debug=SourceLabelBuilder().debug(evidence.source_matches),
        )
        debug_payload = self._debug_payload(
            analysis=analysis,
            documents=documents,
            evidence=evidence,
            draft=draft,
            generation_debug=generation_debug,
            source_label_debug=SourceLabelBuilder().debug(evidence.source_matches),
        )

        return PipelineResult(
            answer=final_answer,
            status=status,
            sources=source_refs,
            verification=verification,
            debug=debug_payload,
        )

    async def _route_documents(
        self,
        analysis: object,
        *,
        workspace_id: str,
        course: str | None,
    ) -> tuple[DocumentCandidate, ...]:
        try:
            return await self._router.route(analysis, workspace_id=workspace_id, course=course)
        except TypeError:
            return await self._router.route(analysis)

    def _rerank(self, spans: object, analysis: object) -> object:
        try:
            return self._reranker.rerank(spans, analysis=analysis)
        except TypeError:
            return self._reranker.rerank(spans)

    def _debug_payload(
        self,
        *,
        analysis: object,
        documents: tuple[DocumentCandidate, ...],
        evidence: EvidencePack,
        draft: object,
        generation_debug: dict[str, object] | None = None,
        source_label_debug: list[dict[str, object]],
    ) -> dict[str, object]:
        query_plan = getattr(analysis, "query_plan", None)
        try:
            query_plan_dict = asdict(query_plan) if query_plan is not None else {}
        except TypeError:
            query_plan_dict = {}

        accepted_decisions = list(getattr(evidence, "decisions", ()))
        all_decisions = list(getattr(self._pack_builder, "last_decisions", ()))
        discarded_decisions = [
            decision
            for decision in all_decisions
            if getattr(decision, "status", "") == "discarded"
        ]
        retriever_discarded = list(getattr(self._retriever, "last_discarded", ()))
        generation = generation_debug or _generation_debug(draft)
        primary_definition_id = _primary_definition_evidence_id(evidence)
        evidence_order_reason = _evidence_order_reason(evidence)
        accepted_count = sum(
            1 for item in evidence.items if getattr(item, "metadata", {}).get("evidence_status") in {"accepted", None, ""}
        )
        return {
            "query_plan": query_plan_dict,
            "course_hint": getattr(analysis, "course_hint", ""),
            "expected_content_types": list(getattr(analysis, "expected_content_types", ())),
            "selected_documents": [_document_debug_dict(document) for document in documents],
            "rejected_documents": [],
            "answer_mode": evidence.answer_mode,
            "llm_model_attempts": generation.get("llm_model_attempts", ()),
            "llm_errors_sanitized": generation.get("llm_errors_sanitized", ()),
            "final_model_used": generation.get("final_model_used"),
            "fallback_used": generation.get("fallback_used", False),
            "answer_fallback_used": generation.get("fallback_used", False),
            "weak_llm_answer_reason": generation.get("weak_llm_answer_reason", ""),
            "primary_definition_evidence_id": primary_definition_id,
            "evidence_order_reason": evidence_order_reason,
            "evidence_items_count": len(evidence.items),
            "accepted_evidence_count": accepted_count,
            "discarded_evidence_count": len(discarded_decisions) + len(retriever_discarded),
            "accepted_evidence": [asdict(item) for item in evidence.items],
            "accepted_decisions": [_decision_dict(decision) for decision in accepted_decisions],
            "discarded_evidence": [_discarded_evidence_dict(item) for item in retriever_discarded],
            "discarded_decisions": [_decision_dict(decision) for decision in discarded_decisions],
            "evidence_decisions": [_decision_dict(decision) for decision in all_decisions],
            "source_label_debug": source_label_debug,
            "reranker_score_breakdown": [_reranker_breakdown(item) for item in evidence.items],
        }

    async def _log(
        self,
        *,
        workspace_id: str,
        question: str,
        documents: tuple[DocumentCandidate, ...],
        evidence: EvidencePack,
        final_answer: str,
        final_sources: list[str],
        question_analysis: dict[str, object],
        generation_debug: dict[str, object],
        source_label_debug: list[dict[str, object]],
    ) -> None:
        if self._logger is None or not workspace_id:
            return
        try:
            await self._logger.log_evidence(
                workspace_id=workspace_id,
                question=question,
                question_analysis=question_analysis,
                document_candidates=[_document_debug_dict(document) for document in documents],
                evidence_pack=_evidence_pack_dict(
                    evidence,
                    generation_debug=generation_debug,
                    source_label_debug=source_label_debug,
                    discarded_decisions=[
                        decision
                        for decision in getattr(self._pack_builder, "last_decisions", ())
                        if getattr(decision, "status", "") == "discarded"
                    ],
                ),
                final_answer=final_answer,
                final_sources=final_sources,
            )
        except Exception as exc:  # noqa: BLE001 - logging failures must not break answers
            LOGGER.warning("failed to write evidence log: %s", exc)
            return


def _evidence_pack_dict(
    evidence: EvidencePack,
    *,
    generation_debug: dict[str, object] | None = None,
    source_label_debug: list[dict[str, object]] | None = None,
    discarded_decisions: list[object] | None = None,
) -> dict[str, object]:
    generation = generation_debug or {}
    return {
        "answer_mode": evidence.answer_mode,
        "items": [asdict(item) for item in evidence.items],
        "source_matches": [asdict(source) for source in evidence.source_matches],
        "missing_requirements": list(evidence.missing_requirements),
        "decisions": [asdict(decision) for decision in evidence.decisions],
        "discarded_decisions": [_decision_log_dict(decision) for decision in discarded_decisions or []],
        "generation": generation,
        "source_label_debug": source_label_debug or [],
        "primary_definition_evidence_id": _primary_definition_evidence_id(evidence),
        "answer_fallback_used": bool(generation.get("fallback_used", False)),
        "weak_llm_answer_reason": generation.get("weak_llm_answer_reason", ""),
        "evidence_order_reason": _evidence_order_reason(evidence),
    }


def _decision_dict(decision: object) -> dict[str, object]:
    try:
        return asdict(decision)  # type: ignore[arg-type]
    except TypeError:
        return {
            "evidence_id": getattr(decision, "evidence_id", ""),
            "status": getattr(decision, "status", ""),
            "reasons": list(getattr(decision, "reasons", ())),
            "score": getattr(decision, "score", None),
            "document_id": getattr(decision, "document_id", ""),
            "preview": getattr(decision, "preview", ""),
        }


def _decision_log_dict(decision: object) -> dict[str, object]:
    row = _decision_dict(decision)
    row.pop("preview", None)
    return row


def _discarded_evidence_dict(item: object) -> dict[str, object]:
    return {
        "document_id": getattr(item, "document_id", ""),
        "chunk_id": getattr(item, "chunk_id", ""),
        "score": getattr(item, "score", 0.0),
        "reason": getattr(item, "reason", ""),
        "preview": getattr(item, "preview", ""),
    }


def _document_debug_dict(document: DocumentCandidate) -> dict[str, object]:
    row = asdict(document)
    row["clean_label"] = SourceLabelBuilder().build_document_label(row)
    return row


def _generation_debug(draft: object) -> dict[str, object]:
    model_input = getattr(draft, "model_input", {})
    if isinstance(model_input, dict):
        generation = model_input.get("generation")
        if isinstance(generation, dict):
            return {
                "llm_model_attempts": tuple(generation.get("llm_model_attempts") or ()),
                "llm_errors_sanitized": tuple(generation.get("llm_errors_sanitized") or ()),
                "final_model_used": generation.get("final_model_used"),
                "fallback_used": bool(generation.get("fallback_used", False)),
                "weak_llm_answer_reason": generation.get("weak_llm_answer_reason", ""),
            }
    return {
        "llm_model_attempts": (),
        "llm_errors_sanitized": (),
        "final_model_used": None,
        "fallback_used": False,
        "weak_llm_answer_reason": "",
    }


def _primary_definition_evidence_id(evidence: EvidencePack) -> str:
    for item in evidence.items:
        metadata = getattr(item, "metadata", {})
        if isinstance(metadata, dict) and metadata.get("primary_definition_candidate"):
            return str(getattr(item, "evidence_id", "") or "")
    return ""


def _evidence_order_reason(evidence: EvidencePack) -> str:
    if not evidence.items:
        return ""
    metadata = getattr(evidence.items[0], "metadata", {})
    if isinstance(metadata, dict):
        return str(metadata.get("evidence_order_reason") or "")
    return ""


def _reranker_breakdown(item: object) -> dict[str, object]:
    metadata = getattr(item, "metadata", {})
    if not isinstance(metadata, dict):
        return {}
    breakdown = metadata.get("reranker_score_breakdown")
    if not isinstance(breakdown, dict):
        return {}
    return {
        "evidence_id": getattr(item, "evidence_id", ""),
        "score": getattr(item, "score", None),
        "breakdown": breakdown,
    }


def _append_sources(answer: str, sources: list[str]) -> str:
    clean_answer = answer.strip()
    source_block = "\n".join(f"- {source}" for source in sources)
    return f"{clean_answer}\n\nИсточники:\n{source_block}".strip()


def _can_show_sources(evidence: EvidencePack, verdict: str) -> bool:
    return (
        evidence.answer_mode in {"answer_from_materials", "partial_answer"}
        and verdict != "fail"
        and bool(evidence.source_matches)
    )


def _final_status(draft_status: AnswerStatus, verdict: str) -> AnswerStatus:
    if verdict == "fail":
        return AnswerStatus.INSUFFICIENT_EVIDENCE
    return draft_status
