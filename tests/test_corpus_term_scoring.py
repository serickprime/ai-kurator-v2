import asyncio

from app.rag.document_router import DocumentCardRecord, DocumentRouter
from app.rag.evidence_pack import EvidencePackBuilder
from app.rag.question_analysis import QuestionAnalyzer
from app.rag.term_scoring import CorpusDocumentText, CorpusTermScorer
from app.rag.types import EvidenceSpan


class FakeDocumentCardStore:
    def __init__(self, records: list[DocumentCardRecord]) -> None:
        self.records = records

    async def list_document_cards(
        self,
        *,
        workspace_id: str,
        course: str | None,
        limit: int,
    ) -> list[DocumentCardRecord]:
        del workspace_id, course
        return self.records[:limit]

    async def match_document_cards(
        self,
        *,
        workspace_id: str,
        query_embedding: list[float],
        course: str | None,
        limit: int,
    ) -> list[DocumentCardRecord]:
        del workspace_id, query_embedding, course
        return self.records[:limit]


def test_corpus_term_scorer_downweights_frequent_terms_and_promotes_rare_anchors() -> None:
    scorer = CorpusTermScorer.from_documents(_crowded_documents())

    common = scorer.term_weight("webhook", role="object")
    rare = scorer.term_weight("sha1_hash", role="exact")

    assert common.frequency_class == "common"
    assert common.weight < 0.5
    assert rare.is_anchor
    assert rare.weight > common.weight


def test_document_router_uses_rare_anchor_over_common_webhook() -> None:
    records = _crowded_records()
    scorer = CorpusTermScorer.from_documents(_crowded_documents())
    router = DocumentRouter(store=FakeDocumentCardStore(records), term_scorer=scorer)
    analysis = QuestionAnalyzer().analyze("как проверить YooMoney hash в webhook?")

    candidates = asyncio.run(router.route(analysis, workspace_id="ws", limit=5))

    assert candidates
    assert candidates[0].document_id == "yoomoney-hash"
    assert "sha1_hash" in candidates[0].matched_anchor_terms or "hash" in candidates[0].matched_anchor_terms
    assert any("missing_anchor_terms" in candidate.penalties for candidate in candidates[1:])


def test_evidence_pack_rejects_common_term_only_span() -> None:
    scorer = CorpusTermScorer.from_documents(_crowded_documents())
    analysis = QuestionAnalyzer().analyze("как проверить YooMoney hash в webhook?")
    spans = (
        EvidenceSpan(
            evidence_id="noise",
            document_id="webhook-noise",
            document_title="Webhook overview",
            text="Webhook callback receives a notification and returns 200 OK.",
            score=0.95,
        ),
        EvidenceSpan(
            evidence_id="target",
            document_id="yoomoney-hash",
            document_title="YooMoney webhook hash",
            text="YooMoney webhook проверяет sha1_hash и сравнивает рассчитанный SHA1 hash.",
            score=0.72,
        ),
    )

    pack = EvidencePackBuilder(term_scorer=scorer).build(spans, analysis=analysis)

    assert [item.evidence_id for item in pack.items] == ["target"]


def _crowded_records() -> list[DocumentCardRecord]:
    records = [
        DocumentCardRecord(
            document_id=f"webhook-noise-{index}",
            filename=f"webhook-noise-{index}.md",
            title=f"Webhook overview {index}",
            summary="Generic webhook callback material without payment signature details.",
            topics=("webhook", "callback", "notification"),
            questions_answered=("How to receive a generic webhook?",),
            entities=("webhook",),
            task_types=("setup",),
            quality_score=0.8,
        )
        for index in range(10)
    ]
    records.append(
        DocumentCardRecord(
            document_id="yoomoney-hash",
            filename="yoomoney-hash.md",
            title="YooMoney webhook hash",
            summary="Explains YooMoney webhook signature verification with SHA1 and sha1_hash.",
            topics=("YooMoney", "webhook", "SHA1", "sha1_hash", "hash"),
            questions_answered=("How to verify YooMoney hash in webhook?",),
            entities=("YooMoney", "sha1_hash"),
            task_types=("debug", "source_check"),
            quality_score=0.9,
        )
    )
    return records


def _crowded_documents() -> list[CorpusDocumentText]:
    docs = [
        CorpusDocumentText(
            document_id=f"webhook-noise-{index}",
            title=f"Webhook overview {index}",
            text="Generic webhook callback notification without payment signature details.",
            chunks=("Webhook callback receives generic notification events.",),
        )
        for index in range(10)
    ]
    docs.append(
        CorpusDocumentText(
            document_id="yoomoney-hash",
            title="YooMoney webhook hash",
            text="YooMoney webhook signature verification with SHA1 and sha1_hash.",
            chunks=("YooMoney webhook проверяет sha1_hash и SHA1 hash.",),
        )
    )
    return docs
