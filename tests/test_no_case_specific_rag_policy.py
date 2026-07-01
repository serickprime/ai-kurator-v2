import asyncio
import json

from app.rag.answer_generator import AnswerGenerator
from app.rag.claim_verifier import ClaimVerifier
from app.rag.evidence_pack import EvidencePackBuilder, build_sources
from app.rag.pipeline import EvidenceFirstRagPipeline
from app.rag.reranker import EvidenceReranker
from app.rag.types import (
    DocumentCandidate,
    EvidencePack,
    EvidenceSpan,
    QuestionAnalysis,
    SourceRef,
)


class RecordingLlm:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    async def complete_text(self, messages: list[dict[str, str]]) -> str:
        self.messages = messages
        return "Supported answer from accepted evidence."


def test_answer_generator_does_not_receive_raw_or_discarded_candidates() -> None:
    llm = RecordingLlm()
    analysis = QuestionAnalysis(
        original_question="How do I configure the integration?",
        primary_intent="configure integration",
        task_type="setup",
        must_answer_points=("configuration step",),
    )
    evidence = EvidencePack(
        items=(
            EvidenceSpan(
                evidence_id="ev-accepted",
                document_id="doc-accepted",
                document_title="Accepted lesson",
                text="The accepted evidence describes the configuration step.",
                metadata={
                    "discarded_candidates": "discarded metadata must not enter the prompt",
                    "raw_candidates": "raw metadata must not enter the prompt",
                },
            ),
        )
    )

    asyncio.run(
        AnswerGenerator(llm).generate(
            analysis,
            evidence,
            dialog_context={
                "summary": "safe dialog note",
                "raw_candidates": "raw candidate text must not enter the prompt",
                "discarded_candidates": "discarded candidate text must not enter the prompt",
                "document_candidates": "document candidate text must not enter the prompt",
                "retrieval_candidates": "retrieval candidate text must not enter the prompt",
            },
        )
    )

    prompt = json.dumps(llm.messages, ensure_ascii=False)
    assert "safe dialog note" in prompt
    assert "accepted evidence describes" in prompt
    assert "raw candidate text must not enter" not in prompt
    assert "discarded candidate text must not enter" not in prompt
    assert "document candidate text must not enter" not in prompt
    assert "retrieval candidate text must not enter" not in prompt
    assert "raw metadata must not enter" not in prompt
    assert "discarded metadata must not enter" not in prompt


def test_evidence_pack_has_no_discarded_candidate_channel() -> None:
    pack = EvidencePack(
        items=(
            EvidenceSpan(
                evidence_id="ev-used",
                document_id="doc-used",
                document_title="Used document",
                text="Accepted evidence.",
            ),
        )
    )

    assert not hasattr(pack, "raw_candidates")
    assert not hasattr(pack, "discarded_candidates")
    assert not hasattr(pack, "document_candidates")


def test_sources_are_built_only_from_evidence_pack_source_matches() -> None:
    pack = EvidencePack(
        items=(
            EvidenceSpan(
                evidence_id="ev-used",
                document_id="doc-used",
                document_title="Used document",
                text="Accepted evidence.",
            ),
            EvidenceSpan(
                evidence_id="ev-not-source",
                document_id="doc-not-source",
                document_title="Partial non-source document",
                text="Partial evidence that must not be shown as a source.",
                is_source=False,
            ),
        ),
        source_matches=(
            SourceRef(
                document_id="doc-used",
                document_title="Used document",
                locator="p. 4",
                evidence_id="ev-used",
            ),
        ),
    )

    assert build_sources(pack) == ["Used document, p. 4"]
    assert "Partial non-source document" not in "\n".join(build_sources(pack))


def test_course_hint_and_common_term_do_not_become_sources_without_evidence() -> None:
    pipeline = EvidenceFirstRagPipeline(
        analyzer=GenericQuestionAnalyzer(),
        router=HintOnlyRouter(),
        retriever=NoEvidenceRetriever(),
        reranker=EvidenceReranker(),
        pack_builder=EvidencePackBuilder(),
        answer_generator=AnswerGenerator(),
        verifier=ClaimVerifier(),
    )

    result = asyncio.run(
        pipeline.answer(
            "How do I configure the API request?",
            workspace_id="workspace-1",
            course="Automation Course",
        )
    )

    assert result.sources == ()
    assert "Automation Course" not in result.answer
    assert "Generic API overview" not in result.answer


def test_no_source_answer_modes_clear_sources() -> None:
    for answer_mode in ("general_answer_without_sources", "ask_for_missing_data", "out_of_base"):
        pack = EvidencePack(
            items=(
                EvidenceSpan(
                    evidence_id="ev-1",
                    document_id="doc-1",
                    document_title="Document that must not be cited",
                    text="Text that should not create a source in no-source mode.",
                ),
            ),
            answer_mode=answer_mode,
            source_matches=(
                SourceRef(
                    document_id="doc-1",
                    document_title="Document that must not be cited",
                    evidence_id="ev-1",
                ),
            ),
        )

        assert pack.sources() == ()
        assert build_sources(pack) == []


class GenericQuestionAnalyzer:
    def analyze(self, question: str) -> QuestionAnalysis:
        return QuestionAnalysis(
            original_question=question,
            primary_intent="configure API request",
            task_type="setup",
            source_required=True,
            must_answer_points=("configuration step",),
            query_facets=(),
            keywords=("configure", "api", "request"),
            requested_action="configuration",
            object_terms=("request",),
            common_terms=("api",),
        )


class HintOnlyRouter:
    async def route(
        self,
        analysis: QuestionAnalysis,
        workspace_id: str = "",
        course: str | None = None,
    ) -> tuple[DocumentCandidate, ...]:
        del analysis, workspace_id
        return (
            DocumentCandidate(
                document_id="doc-course-hint",
                title="Generic API overview",
                course=course,
                score=0.9,
                reason="course hint and common term only",
                route="document_card",
            ),
        )


class NoEvidenceRetriever:
    async def retrieve(
        self,
        analysis: QuestionAnalysis,
        documents: tuple[DocumentCandidate, ...],
    ) -> tuple[EvidenceSpan, ...]:
        del analysis, documents
        return ()
