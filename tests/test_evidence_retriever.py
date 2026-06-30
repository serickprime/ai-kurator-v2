import asyncio

from app.rag.evidence_retriever import EvidenceChunkRecord, EvidenceRetriever, SupabaseEvidenceChunkStore
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


class FakeSupabaseClient:
    def __init__(self) -> None:
        self.select_calls: list[tuple[str, dict[str, str]]] = []

    async def rpc(self, name: str, payload: dict[str, object]) -> list[dict[str, object]]:
        del payload
        assert name == "hybrid_match_chunks_in_documents"
        return [
            {
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "section_id": "section-1",
                "content": "Test webhook documentation.",
                "heading": "Test webhook",
                "score": 0.9,
                "vector_score": 0.9,
            }
        ]

    async def select(self, table: str, params: dict[str, str]) -> list[dict[str, object]]:
        self.select_calls.append((table, params))
        assert table == "chunks"
        return [
            {
                "id": "chunk-1",
                "metadata": {
                    "source_kind": "external_docs",
                    "source_uri": "https://docs.n8n.io/webhooks/test",
                    "canonical_url": "https://docs.n8n.io/webhooks/test",
                },
            }
        ]


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


def test_supabase_chunk_store_enriches_rpc_rows_with_chunk_metadata() -> None:
    client = FakeSupabaseClient()
    store = SupabaseEvidenceChunkStore(client)

    records = asyncio.run(
        store.match_chunks(
            workspace_id="workspace-1",
            document_ids=("doc-1",),
            query_text="test webhook",
            query_embedding=None,
            match_count=5,
        )
    )

    assert client.select_calls
    assert records[0].source_uri == "https://docs.n8n.io/webhooks/test"
    assert records[0].metadata["source_kind"] == "external_docs"
