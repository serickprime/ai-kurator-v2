import asyncio
import json

from app.rag.answer_generator import AnswerGenerator
from app.rag.document_router import DocumentCardRecord, DocumentRouter
from app.rag.evidence_pack import EvidencePackBuilder, build_sources
from app.rag.evidence_retriever import EvidenceChunkRecord, EvidenceRetriever
from app.rag.term_scoring import CorpusDocumentText, CorpusTermScorer
from app.rag.types import DocumentCandidate, EvidencePack, EvidenceSpan, QueryFacet, QuestionAnalysis


class RecordingCardStore:
    def __init__(self, records: list[DocumentCardRecord]) -> None:
        self.records = records
        self.list_courses: list[str | None] = []

    async def list_document_cards(
        self,
        *,
        workspace_id: str,
        course: str | None,
        limit: int,
    ) -> list[DocumentCardRecord]:
        del workspace_id, limit
        self.list_courses.append(course)
        if course:
            return [record for record in self.records if record.course == course]
        return self.records


class RecordingChunkStore:
    def __init__(self, records: list[EvidenceChunkRecord]) -> None:
        self.records = records
        self.calls: list[tuple[str, ...]] = []

    async def match_chunks(
        self,
        *,
        workspace_id: str,
        document_ids: tuple[str, ...],
        query_text: str,
        query_embedding: list[float] | None,
        match_count: int,
    ) -> list[EvidenceChunkRecord]:
        del workspace_id, query_text, query_embedding, match_count
        self.calls.append(document_ids)
        allowed = set(document_ids)
        return [record for record in self.records if record.document_id in allowed]


class RecordingLlm:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    async def complete_text(self, messages: list[dict[str, str]]) -> str:
        self.messages = messages
        return "Use the accepted evidence only."


def test_no_course_question_does_not_filter_whole_base_by_course() -> None:
    store = RecordingCardStore(
        [
            DocumentCardRecord(
                document_id="doc-1",
                filename="homework.md",
                title="Homework submission",
                summary="Submit homework in the platform.",
                topics=("homework",),
                questions_answered=("How do I submit homework?",),
                content_types=("homework_task",),
            )
        ]
    )
    analysis = QuestionAnalysis(
        original_question="How do I submit homework?",
        task_type="admin",
        object_terms=("homework",),
        requested_action="submit",
        expected_content_types=("homework_task",),
    )

    candidates = asyncio.run(DocumentRouter(store=store).route(analysis, workspace_id="ws"))

    assert candidates
    assert store.list_courses == [None]


def test_course_hint_is_soft_scope_not_hard_filter() -> None:
    analysis = QuestionAnalysis(
        original_question="How do I submit homework?",
        primary_intent="submit homework",
        task_type="admin",
        object_terms=("homework",),
        requested_action="submit",
        action_terms=("submit",),
        expected_content_types=("homework_task",),
        course_hint="Data Bootcamp",
        course_hint_confidence=0.92,
        query_facets=(
            QueryFacet(role="action", text="submit", importance=0.9),
            QueryFacet(role="object", text="homework", importance=1.0),
        ),
    )
    store = RecordingCardStore(
        [
            DocumentCardRecord(
                document_id="course-overview",
                filename="overview.md",
                title="Data Bootcamp overview",
                course="Data Bootcamp",
                summary="Course modules, schedule, and access terms.",
                content_types=("course_structure",),
                quality_score=0.9,
            ),
            DocumentCardRecord(
                document_id="homework-submit",
                filename="homework-submit.md",
                title="Homework submission rules",
                course="Other Course",
                summary="Students submit homework assignments through the platform form.",
                topics=("homework", "submission"),
                questions_answered=("How do I submit homework?",),
                content_types=("homework_task",),
                quality_score=0.8,
            ),
        ]
    )

    candidates = asyncio.run(DocumentRouter(store=store, min_score=0.0).route(analysis, workspace_id="ws", limit=2))

    assert candidates[0].document_id == "homework-submit"
    assert candidates[0].matched_content_types == ("homework_task",)
    assert any(candidate.document_id == "course-overview" for candidate in candidates)


def test_course_hint_is_not_sent_to_answer_generator_prompt() -> None:
    llm = RecordingLlm()
    analysis = QuestionAnalysis(
        original_question="How do I submit homework?",
        task_type="admin",
        course_hint="Data Bootcamp",
        domain_hint="Learning Portal",
        object_terms=("homework",),
        requested_action="submit",
    )
    evidence = EvidencePack(
        items=(
            EvidenceSpan(
                evidence_id="ev-1",
                document_id="doc-1",
                document_title="Homework guide",
                text="Submit homework through the assignment form.",
            ),
        )
    )

    asyncio.run(AnswerGenerator(llm).generate(analysis, evidence))
    prompt = json.dumps(llm.messages, ensure_ascii=False)

    assert "Submit homework through the assignment form" in prompt
    assert "Data Bootcamp" not in prompt
    assert "Learning Portal" not in prompt


def test_common_term_only_match_gets_penalty() -> None:
    scorer = CorpusTermScorer.from_documents(
        [
            CorpusDocumentText(document_id=f"doc-{index}", text="api api api general", title=f"Doc {index}")
            for index in range(8)
        ]
    )
    analysis = QuestionAnalysis(
        original_question="API",
        query_facets=(QueryFacet(role="platform", text="api", importance=0.6),),
        common_terms=("api",),
        keywords=("api",),
    )
    router = DocumentRouter(
        store=RecordingCardStore(
            [
                DocumentCardRecord(
                    document_id="api-overview",
                    filename="api.md",
                    title="API overview",
                    summary="General API introduction.",
                    topics=("api",),
                )
            ]
        ),
        term_scorer=scorer,
        min_score=0.0,
    )

    candidates = asyncio.run(router.route(analysis, workspace_id="ws"))

    assert candidates
    assert "general_common_term_only" in candidates[0].penalties


def test_wrong_content_type_is_penalized() -> None:
    analysis = QuestionAnalysis(
        original_question="How is homework reviewed?",
        task_type="admin",
        object_terms=("homework", "review"),
        requested_action="review",
        expected_content_types=("homework_review_rules",),
        query_facets=(
            QueryFacet(role="action", text="review", importance=0.9),
            QueryFacet(role="object", text="homework", importance=1.0),
        ),
    )
    router = DocumentRouter(
        store=RecordingCardStore(
            [
                DocumentCardRecord(
                    document_id="lesson",
                    filename="lesson.md",
                    title="Homework lesson",
                    summary="This lesson mentions homework review in passing.",
                    content_types=("lesson_material",),
                    quality_score=0.8,
                )
            ]
        ),
        min_score=0.0,
    )

    candidates = asyncio.run(router.route(analysis, workspace_id="ws"))

    assert candidates
    assert "wrong_content_type_penalty" in candidates[0].penalties
    assert candidates[0].score_breakdown["content_type_match"] == 0.0


def test_retriever_searches_only_selected_documents() -> None:
    store = RecordingChunkStore(
        [
            EvidenceChunkRecord(
                chunk_id="selected-1",
                document_id="selected-doc",
                document_title="Selected",
                content="Submit homework through the assignment form.",
                score=0.8,
            ),
            EvidenceChunkRecord(
                chunk_id="discarded-1",
                document_id="not-selected-doc",
                document_title="Noise",
                content="This unrelated document also says homework.",
                score=0.9,
            ),
        ]
    )
    analysis = QuestionAnalysis(
        original_question="How do I submit homework?",
        object_terms=("homework",),
        requested_action="submit",
    )
    documents = (
        DocumentCandidate(document_id="selected-doc", title="Selected", score=0.9),
        DocumentCandidate(document_id="not-selected-doc", title="Noise", score=0.8),
    )

    spans = asyncio.run(EvidenceRetriever(chunk_store=store, workspace_id="ws").retrieve(analysis, documents))

    assert store.calls == [("selected-doc",)]
    assert [span.document_id for span in spans] == ["selected-doc"]


def test_evidence_pack_records_decisions_and_sources_only_selected_items() -> None:
    analysis = QuestionAnalysis(
        original_question="How do I store service role keys?",
        object_terms=("service role", "keys"),
        expected_content_types=("lesson_material",),
    )
    builder = EvidencePackBuilder()
    pack = builder.build(
        (
            EvidenceSpan(
                evidence_id="good",
                document_id="secure-doc",
                document_title="Security lesson",
                text="Store service role keys only on the server.",
                score=0.72,
                is_source=True,
            ),
            EvidenceSpan(
                evidence_id="bad",
                document_id="noise-doc",
                document_title="General lesson",
                text="This text is about a public API key.",
                score=0.7,
                is_source=True,
            ),
        ),
        analysis=analysis,
    )

    assert [item.evidence_id for item in pack.items] == ["good"]
    assert build_sources(pack) == ["Security lesson"]
    assert any(decision.status == "discarded" for decision in builder.last_decisions)
    assert all(decision.status != "discarded" for decision in pack.decisions)


def test_broad_external_landing_page_without_requested_object_is_not_sufficient() -> None:
    analysis = QuestionAnalysis(
        original_question="According to official docs, how does test webhook work?",
        needs_official_docs=True,
        needs_external_docs=True,
        expected_content_types=("official_docs", "external_docs"),
        expected_source_kinds=("external_docs",),
        object_terms=("test", "webhook"),
    )
    chunk_store = RecordingChunkStore(
        [
            EvidenceChunkRecord(
                chunk_id="broad-doc",
                document_id="external-build",
                document_title="Build docs",
                heading="Foundations",
                content="Build workflows and test the data moving through workflows.",
                score=0.9,
                metadata={
                    "source_kind": "external_docs",
                    "content_type": ["official_docs", "external_docs"],
                    "source_uri": "https://docs.example.com/build",
                },
            )
        ]
    )
    documents = (DocumentCandidate(document_id="external-build", title="Build docs", score=0.9),)

    spans = asyncio.run(EvidenceRetriever(chunk_store=chunk_store, workspace_id="ws").retrieve(analysis, documents))
    builder = EvidencePackBuilder()
    pack = builder.build(spans, analysis=analysis)

    assert spans
    assert pack.answer_mode == "out_of_base"
    assert pack.sources() == ()
    assert build_sources(pack) == []
    assert any("broad_official_doc_without_requested_object" in decision.reasons for decision in builder.last_decisions)


def test_no_evidence_modes_do_not_create_fake_sources() -> None:
    analysis = QuestionAnalysis(original_question="Unknown question", source_required=True)
    pack = EvidencePackBuilder().build((), analysis=analysis)

    assert pack.answer_mode == "out_of_base"
    assert pack.sources() == ()
    assert build_sources(pack) == []


def test_ambiguous_missing_input_keeps_ask_for_missing_data_mode() -> None:
    analysis = QuestionAnalysis(
        original_question="It is broken, what should I do?",
        task_type="debug",
        missing_input_requirements=("exact error text", "where it happens"),
        object_terms=("it",),
    )
    pack = EvidencePackBuilder().build(
        (
            EvidenceSpan(
                evidence_id="ev-1",
                document_id="doc-1",
                document_title="Generic troubleshooting",
                text="Troubleshooting requires the exact error text.",
                score=0.8,
            ),
        ),
        analysis=analysis,
    )

    assert pack.answer_mode == "ask_for_missing_data"
    assert pack.sources() == ()
