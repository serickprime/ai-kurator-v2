import asyncio
import json

from app.rag.answer_generator import AnswerGenerator
from app.rag.evidence_pack import EvidencePackBuilder, build_sources
from app.rag.reranker import EvidenceReranker
from app.rag.types import EvidencePack, EvidenceSpan, QuestionAnalysis, SourceRef


class FailingLlm:
    last_metadata = None

    async def complete_text(self, messages: list[dict[str, str]]) -> str:
        del messages
        raise RuntimeError("OpenRouter request failed for model test/model: 400 bad request")


def _setup_analysis() -> QuestionAnalysis:
    return QuestionAnalysis(
        original_question="How do I configure the connector rules file?",
        primary_intent="configure connector rules file",
        task_type="setup",
        requested_action="configure",
        object_terms=("connector", "rules", "file"),
        exact_terms=("rules.md",),
        must_answer_points=("configuration step", "file name", "result check"),
        evidence_questions=(
            "Does the source explain the configuration step?",
            "Does the source name the file or setting?",
            "Does the source show how to check the result?",
        ),
    )


def test_reranker_prefers_actionable_setup_evidence_over_overview() -> None:
    analysis = _setup_analysis()
    overview = EvidenceSpan(
        evidence_id="overview",
        document_id="doc-1",
        document_title="Connector lesson",
        text="Connector setup overview. This page introduces the interface, limits, and lesson navigation.",
        score=0.72,
    )
    practical = EvidenceSpan(
        evidence_id="practical",
        document_id="doc-1",
        document_title="Connector lesson",
        text=(
            "1. Create rules.md in the project folder.\n"
            "2. Add the connector rules and required API_URL setting.\n"
            "3. Run connector status and check that the rules file is loaded."
        ),
        score=0.52,
    )

    reranked = EvidenceReranker().rerank((overview, practical), analysis=analysis)

    assert reranked[0].evidence_id == "practical"
    assert reranked[0].metadata["reranker_score_breakdown"]["actionability"] > 0


def test_evidence_pack_does_not_fill_with_weak_chunks() -> None:
    analysis = _setup_analysis()
    reranked = EvidenceReranker().rerank(
        (
            EvidenceSpan(
                evidence_id="weak",
                document_id="doc-1",
                document_title="Connector lesson",
                text="Connector rules file overview. This is mostly lesson navigation and interface context.",
                score=0.28,
            ),
            EvidenceSpan(
                evidence_id="strong",
                document_id="doc-1",
                document_title="Connector lesson",
                text="Create rules.md, add the connector rules, set API_URL, then run connector status to check loading.",
                score=0.44,
            ),
        ),
        analysis=analysis,
    )

    builder = EvidencePackBuilder()
    pack = builder.build(reranked, analysis=analysis)

    assert [item.evidence_id for item in pack.items] == ["strong"]
    assert any(decision.evidence_id == "weak" and decision.status != "accepted" for decision in builder.last_decisions)


def test_source_labels_clean_bad_title_and_locator() -> None:
    pack = EvidencePack(
        items=(
            EvidenceSpan(
                evidence_id="ev-1",
                document_id="doc-1",
                document_title="Название файла:",
                locator="Прочее",
                text="Create the rules file.",
                metadata={"filename": "connector_setup.md"},
            ),
        ),
        source_matches=(
            SourceRef(
                document_id="doc-1",
                document_title="Название файла:",
                locator="Прочее",
                evidence_id="ev-1",
                metadata={"filename": "connector_setup.md"},
            ),
        ),
    )

    sources = build_sources(pack)

    assert sources == ["connector_setup"]
    assert "Название файла" not in sources[0]
    assert "Прочее" not in sources[0]


def test_deterministic_fallback_is_not_raw_evidence_dump() -> None:
    analysis = _setup_analysis()
    evidence = EvidencePack(
        items=(
            EvidenceSpan(
                evidence_id="ev-1",
                document_id="doc-1",
                document_title="Connector lesson",
                text=(
                    "====================\n"
                    "СТРАНИЦА 2\n"
                    "Текст страницы:\n"
                    "1. Create rules.md in the project folder.\n"
                    "2. Add the connector rules and required API_URL setting.\n"
                    "3. Run connector status and check that the rules file is loaded."
                ),
            ),
        )
    )

    draft = asyncio.run(AnswerGenerator(FailingLlm()).generate(analysis, evidence))

    assert draft.model_input["generation"]["fallback_used"] is True
    assert "В материалах указано" in draft.text
    assert "СТРАНИЦА" not in draft.text
    assert "Текст страницы" not in draft.text
    assert "evidence pack" not in draft.text.lower()


def test_answer_generator_prompt_still_excludes_raw_candidates() -> None:
    class RecordingLlm:
        def __init__(self) -> None:
            self.messages: list[dict[str, str]] = []

        async def complete_text(self, messages: list[dict[str, str]]) -> str:
            self.messages = messages
            return "**Safe answer.**"

    llm = RecordingLlm()
    asyncio.run(
        AnswerGenerator(llm).generate(
            _setup_analysis(),
            EvidencePack(
                items=(
                    EvidenceSpan(
                        evidence_id="ev-1",
                        document_id="doc-1",
                        document_title="Connector lesson",
                        text="Create rules.md in the project folder.",
                    ),
                )
            ),
            dialog_context={"raw_candidates": "must not enter", "summary": "safe context"},
        )
    )

    prompt = json.dumps(llm.messages, ensure_ascii=False)
    assert "safe context" in prompt
    assert "must not enter" not in prompt


def test_answer_generator_rejects_source_only_model_answer() -> None:
    class SourceOnlyLlm:
        async def complete_text(self, messages: list[dict[str, str]]) -> str:
            del messages
            return "(Build | n8n Docs)"

    evidence = EvidencePack(
        items=(
            EvidenceSpan(
                evidence_id="ev-1",
                document_id="doc-1",
                document_title="Build | n8n Docs",
                text="Build workflows in n8n, from first draft to production. Use this space to learn workflow basics.",
            ),
        )
    )

    draft = asyncio.run(AnswerGenerator(SourceOnlyLlm()).generate(_setup_analysis(), evidence))

    assert draft.model_input["generation"]["fallback_used"] is True
    assert "Build workflows in n8n" in draft.text
    assert draft.text != "(Build | n8n Docs)"


def test_answer_generator_strips_decorative_markdown_from_model_answer() -> None:
    class MarkdownLlm:
        async def complete_text(self, messages: list[dict[str, str]]) -> str:
            del messages
            return "**Safe answer.**"

    draft = asyncio.run(
        AnswerGenerator(MarkdownLlm()).generate(
            _setup_analysis(),
            EvidencePack(
                items=(
                    EvidenceSpan(
                        evidence_id="ev-1",
                        document_id="doc-1",
                        document_title="Connector lesson",
                        text="Create rules.md in the project folder.",
                    ),
                )
            ),
        )
    )

    assert draft.text == "Safe answer."
