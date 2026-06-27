"""Evidence-first RAG pipeline orchestration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Protocol

from app.rag.answer_generator import AnswerGenerator
from app.rag.claim_verifier import ClaimVerifier
from app.rag.document_router import DocumentRouter
from app.rag.evidence_pack import EvidencePackBuilder, build_sources
from app.rag.evidence_retriever import EvidenceRetriever
from app.rag.question_analysis import QuestionAnalyzer
from app.rag.reranker import EvidenceReranker
from app.rag.types import AnswerStatus, DocumentCandidate, EvidencePack, PipelineResult


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
        documents = await self._route_documents(analysis, workspace_id=workspace_id, course=course)
        spans = await self._retriever.retrieve(analysis, documents)
        reranked = self._reranker.rerank(spans)
        evidence = self._pack_builder.build(reranked, analysis=analysis)
        draft = await self._answer_generator.generate(analysis, evidence, dialog_context=dialog_context)
        verification = self._verifier.verify(draft, evidence)

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
        )

        return PipelineResult(
            answer=final_answer,
            status=status,
            sources=source_refs,
            verification=verification,
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
    ) -> None:
        if self._logger is None or not workspace_id:
            return
        try:
            await self._logger.log_evidence(
                workspace_id=workspace_id,
                question=question,
                question_analysis=question_analysis,
                document_candidates=[asdict(document) for document in documents],
                evidence_pack=_evidence_pack_dict(evidence),
                final_answer=final_answer,
                final_sources=final_sources,
            )
        except Exception:
            return


def _evidence_pack_dict(evidence: EvidencePack) -> dict[str, object]:
    return {
        "answer_mode": evidence.answer_mode,
        "items": [asdict(item) for item in evidence.items],
        "source_matches": [asdict(source) for source in evidence.source_matches],
        "missing_requirements": list(evidence.missing_requirements),
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
