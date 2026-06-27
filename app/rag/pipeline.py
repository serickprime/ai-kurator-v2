"""Evidence-first RAG pipeline orchestration."""

from app.rag.answer_generator import AnswerGenerator
from app.rag.claim_verifier import ClaimVerifier
from app.rag.document_router import DocumentRouter
from app.rag.evidence_pack import EvidencePackBuilder
from app.rag.evidence_retriever import EvidenceRetriever
from app.rag.question_analysis import QuestionAnalyzer
from app.rag.reranker import EvidenceReranker
from app.rag.types import AnswerStatus, PipelineResult


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
    ) -> None:
        self._analyzer = analyzer
        self._router = router
        self._retriever = retriever
        self._reranker = reranker
        self._pack_builder = pack_builder
        self._answer_generator = answer_generator
        self._verifier = verifier

    async def answer(self, question: str) -> PipelineResult:
        """Run the evidence-first pipeline for one question."""
        analysis = self._analyzer.analyze(question)
        documents = await self._router.route(analysis)
        spans = await self._retriever.retrieve(analysis, documents)
        reranked = self._reranker.rerank(spans)
        evidence = self._pack_builder.build(reranked)
        draft = await self._answer_generator.generate(analysis, evidence)
        verification = self._verifier.verify(draft, evidence)

        if not verification.is_supported:
            return PipelineResult(
                answer="В evidence pack нет достаточного подтверждения для надежного ответа.",
                status=AnswerStatus.INSUFFICIENT_EVIDENCE,
                sources=(),
                verification=verification,
            )

        return PipelineResult(
            answer=draft.text,
            status=draft.status,
            sources=evidence.sources(),
            verification=verification,
        )
