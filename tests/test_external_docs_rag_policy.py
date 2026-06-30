import asyncio
from types import SimpleNamespace

from app.external_docs.policy import freshness_required, should_use_external_docs
from app.rag.answer_generator import generate_answer
from app.rag.question_analysis import QuestionAnalyzer
from app.rag.types import AnswerStatus, EvidencePack, EvidenceSpan, QuestionAnalysis


def test_local_evidence_has_priority_over_external_docs() -> None:
    analysis = QuestionAnalysis(original_question="according to official docs?", needs_external_docs=True)
    local_pack = EvidencePack(
        items=(
            EvidenceSpan(
                evidence_id="e1",
                document_id="local-1",
                document_title="Local lesson",
                text="Local evidence answers the question.",
            ),
        ),
        answer_mode="answer_from_materials",
    )

    assert not should_use_external_docs(analysis, local_pack)


def test_external_docs_allowed_when_local_evidence_is_insufficient() -> None:
    analysis = QuestionAnalysis(original_question="latest n8n docs?", needs_external_docs=True)
    weak_pack = SimpleNamespace(answer_mode="out_of_base", items=(), missing_requirements=("official docs",))

    assert should_use_external_docs(analysis, weak_pack)


def test_question_analysis_marks_latest_docs_questions() -> None:
    analysis = QuestionAnalyzer().analyze("по последней документации n8n как настроить node?")

    assert analysis.needs_external_docs
    assert analysis.freshness_required
    assert analysis.expected_source_kinds == ("external_docs",)
    assert "external_docs" in analysis.expected_content_types


def test_question_analysis_keeps_course_material_questions_local_first() -> None:
    analysis = QuestionAnalyzer().analyze("что было в уроке про CLAUDE.md?")

    assert not analysis.needs_external_docs
    assert not freshness_required(analysis.original_question)


def test_external_out_of_base_answer_mentions_indexed_docs_object() -> None:
    analysis = QuestionAnalysis(
        original_question="According to official docs, how does Custom Widget work?",
        needs_official_docs=True,
        needs_external_docs=True,
        expected_content_types=("official_docs", "external_docs"),
        expected_source_kinds=("external_docs",),
        object_terms=("Custom", "Widget"),
    )
    evidence = EvidencePack(answer_mode="out_of_base")

    draft = asyncio.run(generate_answer(analysis, evidence))

    assert draft.status == AnswerStatus.NEEDS_CLARIFICATION
    assert draft.answer_mode == "out_of_base"
    assert "Custom Widget" in draft.text
    assert "проиндексированной официальной документации" in draft.text
