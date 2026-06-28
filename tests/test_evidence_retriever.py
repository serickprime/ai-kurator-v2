import asyncio

from app.rag.evidence_retriever import EvidenceChunkRecord, EvidenceRetriever
from app.rag.question_analysis import QuestionAnalyzer
from app.rag.types import DocumentCandidate


class FakeChunkStore:
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


def test_evidence_retriever_uses_narrow_top_document_for_regular_questions() -> None:
    store = FakeChunkStore(
        [
            EvidenceChunkRecord(
                chunk_id="install-1",
                document_id="install-doc",
                document_title="Install",
                content="n8n запускают локально через Docker и открывают на localhost.",
                score=0.8,
            ),
            EvidenceChunkRecord(
                chunk_id="payment-1",
                document_id="payment-doc",
                document_title="Payments",
                content="ЮMoney workflow тоже упоминает n8n.",
                score=0.9,
            ),
        ]
    )
    analysis = QuestionAnalyzer().analyze("как установить n8n локально?")
    documents = (
        DocumentCandidate(document_id="install-doc", title="Install", score=0.9),
        DocumentCandidate(document_id="payment-doc", title="Payments", score=0.8),
    )

    spans = asyncio.run(EvidenceRetriever(chunk_store=store, workspace_id="ws").retrieve(analysis, documents))

    assert store.calls == [("install-doc",)]
    assert [span.document_id for span in spans] == ["install-doc"]


def test_evidence_retriever_discards_chunks_without_question_object() -> None:
    store = FakeChunkStore(
        [
            EvidenceChunkRecord(
                chunk_id="wrong-1",
                document_id="doc-1",
                document_title="General",
                content="Материал объясняет оплату и workflow.",
                score=0.9,
            ),
        ]
    )
    analysis = QuestionAnalyzer().analyze("как хранить картофель?")
    documents = (DocumentCandidate(document_id="doc-1", title="General", score=0.9),)

    retriever = EvidenceRetriever(chunk_store=store, workspace_id="ws")
    spans = asyncio.run(retriever.retrieve(analysis, documents))

    assert spans == ()
    assert retriever.last_discarded
    assert retriever.last_discarded[0].reason == "missing primary object terms"
