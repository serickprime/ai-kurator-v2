import asyncio
import json

from app.rag.answer_generator import AnswerGenerator
from app.rag.claim_verifier import ClaimVerifier
from app.rag.evidence_pack import build_sources
from app.rag.question_analysis import QuestionAnalyzer
from app.rag.types import AnswerDraft, AnswerStatus, EvidencePack, EvidenceSpan, SourceRef


class FakeLlm:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    async def complete_text(self, messages: list[dict[str, str]]) -> str:
        self.messages = messages
        return "Подтверждено: n8n можно запустить локально через Docker."


def test_generation_does_not_see_discarded_candidates() -> None:
    llm = FakeLlm()
    analysis = QuestionAnalyzer().analyze("как установить n8n локально?")
    evidence = EvidencePack(
        items=(
            EvidenceSpan(
                evidence_id="ev-install",
                document_id="doc-install",
                document_title="Локальная установка n8n",
                text="n8n можно запустить локально через Docker.",
            ),
        )
    )

    asyncio.run(
        AnswerGenerator(llm).generate(
            analysis,
            evidence,
            dialog_context={
                "summary": "follow-up про локальную установку",
                "discarded_candidates": "ЮMoney в n8n",
                "raw_candidates": "сырой нерелевантный чанк",
            },
        )
    )

    prompt = json.dumps(llm.messages, ensure_ascii=False)
    assert "ЮMoney" not in prompt
    assert "сырой нерелевантный чанк" not in prompt
    assert "Локальная установка n8n" in prompt


def test_sources_only_from_evidence_pack() -> None:
    evidence = EvidencePack(
        items=(
            EvidenceSpan(
                evidence_id="ev-used",
                document_id="doc-used",
                document_title="Used lesson",
                text="Used evidence.",
            ),
            EvidenceSpan(
                evidence_id="ev-partial",
                document_id="doc-partial",
                document_title="Partial lesson",
                text="Partial evidence that is not marked as a source.",
                is_source=False,
            ),
        ),
        source_matches=(
            SourceRef(
                document_id="doc-used",
                document_title="Used lesson",
                locator="p. 2",
                evidence_id="ev-used",
            ),
        ),
    )

    sources = build_sources(evidence)

    assert sources == ["Used lesson, p. 2"]
    assert "Partial lesson" not in " ".join(sources)


def test_unsupported_claim_removed() -> None:
    draft = AnswerDraft(
        text="Use Docker to start n8n locally. The default password is admin.",
        status=AnswerStatus.ANSWERED,
    )
    evidence = EvidencePack(
        items=(
            EvidenceSpan(
                evidence_id="ev-1",
                document_id="doc-1",
                document_title="Install",
                text="Use Docker to start n8n locally.",
            ),
        )
    )

    report = ClaimVerifier().verify(draft, evidence)

    assert report.verdict == "rewrite"
    assert "Docker" in report.safe_answer
    assert "default password" not in report.safe_answer
    assert report.unsupported_claims


def test_no_sources_when_answer_mode_not_materials() -> None:
    for answer_mode in ("ask_for_missing_data", "general_answer_without_sources"):
        evidence = EvidencePack(
            items=(
                EvidenceSpan(
                    evidence_id="ev-1",
                    document_id="doc-1",
                    document_title="General note",
                    text="General evidence.",
                ),
            ),
            answer_mode=answer_mode,
            source_matches=(
                SourceRef(
                    document_id="doc-1",
                    document_title="General note",
                    evidence_id="ev-1",
                ),
            ),
        )

        assert build_sources(evidence) == []
        assert evidence.sources() == ()
