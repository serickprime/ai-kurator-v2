import asyncio

from app.rag.answer_generator import generate_answer
from app.rag.claim_verifier import ClaimVerifier
from app.rag.evidence_pack import EvidencePackBuilder
from app.rag.pipeline import EvidenceFirstRagPipeline
from app.rag.reranker import EvidenceReranker
from app.rag.types import AnswerStatus, DocumentCandidate, EvidenceSpan, QuestionAnalysis


class RecordingAnalyzer:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def analyze(self, question: str) -> QuestionAnalysis:
        self.calls.append("question_analysis")
        return QuestionAnalysis(original_question=question, must_answer_points=("запуск",))


class RecordingRouter:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def route(
        self,
        analysis: QuestionAnalysis,
        workspace_id: str = "",
        course: str | None = None,
    ) -> tuple[DocumentCandidate, ...]:
        del analysis, workspace_id, course
        self.calls.append("document_router")
        return (DocumentCandidate(document_id="doc-install", title="Установка n8n"),)


class RecordingRetriever:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def retrieve(
        self,
        analysis: QuestionAnalysis,
        documents: tuple[DocumentCandidate, ...],
    ) -> tuple[EvidenceSpan, ...]:
        del analysis
        self.calls.append("evidence_retrieval")
        assert documents[0].document_id == "doc-install"
        return (
            EvidenceSpan(
                evidence_id="ev-1",
                document_id="doc-install",
                document_title="Установка n8n",
                text="Запуск n8n локально описан в материале.",
                score=0.72,
            ),
        )


class RecordingAnswerGenerator:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def generate(
        self,
        analysis: QuestionAnalysis,
        evidence: object,
        dialog_context: object | None = None,
    ) -> object:
        self.calls.append("answer_generation")
        return await generate_answer(analysis, evidence, dialog_context=dialog_context)


class RecordingVerifier(ClaimVerifier):
    def __init__(self, calls: list[str]) -> None:
        super().__init__()
        self.calls = calls

    def verify(self, draft: object, evidence: object) -> object:
        self.calls.append("claim_verifier")
        return super().verify(draft, evidence)


def test_pipeline_still_runs_evidence_first_order() -> None:
    calls: list[str] = []
    pipeline = EvidenceFirstRagPipeline(
        analyzer=RecordingAnalyzer(calls),
        router=RecordingRouter(calls),
        retriever=RecordingRetriever(calls),
        reranker=EvidenceReranker(),
        pack_builder=EvidencePackBuilder(),
        answer_generator=RecordingAnswerGenerator(calls),
        verifier=RecordingVerifier(calls),
    )

    result = asyncio.run(pipeline.answer("как установить n8n локально?", workspace_id="workspace-1"))

    assert calls == [
        "question_analysis",
        "document_router",
        "evidence_retrieval",
        "answer_generation",
        "claim_verifier",
    ]
    assert result.sources


def test_pipeline_short_circuits_small_talk_without_retrieval() -> None:
    calls: list[str] = []
    pipeline = EvidenceFirstRagPipeline(
        analyzer=SmallTalkAnalyzer(calls),
        router=ExplodingRouter(),
        retriever=ExplodingRetriever(),
        reranker=EvidenceReranker(),
        pack_builder=EvidencePackBuilder(),
        answer_generator=RecordingAnswerGenerator(calls),
        verifier=RecordingVerifier(calls),
    )

    result = asyncio.run(pipeline.answer("привет", workspace_id="workspace-1"))

    assert calls == ["question_analysis", "answer_generation", "claim_verifier"]
    assert result.status == AnswerStatus.ANSWERED
    assert result.sources == ()
    assert "Привет" in result.answer


class SmallTalkAnalyzer:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def analyze(self, question: str) -> QuestionAnalysis:
        self.calls.append("question_analysis")
        return QuestionAnalysis(
            original_question=question,
            primary_intent="поприветствовать пользователя",
            task_type="general",
            source_required=False,
            answer_scope="general",
            intent="small_talk",
        )


class ExplodingRouter:
    async def route(self, *args: object, **kwargs: object) -> tuple[DocumentCandidate, ...]:
        raise AssertionError("Router must not run for source-free small talk")


class ExplodingRetriever:
    async def retrieve(self, *args: object, **kwargs: object) -> tuple[EvidenceSpan, ...]:
        raise AssertionError("Retriever must not run for source-free small talk")
