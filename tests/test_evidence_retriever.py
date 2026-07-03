import asyncio

from app.rag.evidence_retriever import EvidenceChunkRecord, EvidenceRetriever, SupabaseEvidenceChunkStore
from app.rag.question_analysis import QuestionAnalyzer
from app.rag.types import DocumentCandidate


class FakeChunkStore:
    def __init__(self, records: list[EvidenceChunkRecord]) -> None:
        self.records = records
        self.calls: list[tuple[str, ...]] = []
        self.query_texts: list[str] = []

    async def match_chunks(
        self,
        *,
        workspace_id: str,
        document_ids: tuple[str, ...],
        query_text: str,
        query_embedding: list[float] | None,
        match_count: int,
    ) -> list[EvidenceChunkRecord]:
        del workspace_id, query_embedding, match_count
        self.calls.append(document_ids)
        self.query_texts.append(query_text)
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


def test_evidence_retriever_enriches_telegram_bot_api_send_message_query() -> None:
    store = FakeChunkStore(
        [
            EvidenceChunkRecord(
                chunk_id="send-message",
                document_id="telegram-doc",
                document_title="Telegram Bot API",
                heading="sendMessage",
                content="Use sendMessage to send text messages. Required parameters include chat_id and text.",
                score=0.2,
            ),
            EvidenceChunkRecord(
                chunk_id="overview",
                document_id="telegram-doc",
                document_title="Telegram Bot API",
                heading="Overview",
                content="Telegram Bot API provides methods for working with bots.",
                score=0.1,
            ),
        ]
    )
    analysis = QuestionAnalyzer().analyze("как отправить сообщение через Telegram Bot API?")
    documents = (DocumentCandidate(document_id="telegram-doc", title="Telegram Bot API", score=0.9),)

    spans = asyncio.run(EvidenceRetriever(chunk_store=store, workspace_id="ws", min_score=0.0).retrieve(analysis, documents))

    assert store.query_texts
    assert "sendMessage" in store.query_texts[-1]
    assert "chat_id" in store.query_texts[-1]
    assert "text" in store.query_texts[-1]
    assert spans
    assert spans[0].evidence_id == "send-message"
    assert "chat_id" in spans[0].text


def test_evidence_retriever_enriches_n8n_http_request_query() -> None:
    store = FakeChunkStore(
        [
            EvidenceChunkRecord(
                chunk_id="http-request-node",
                document_id="n8n-doc",
                document_title="n8n Docs",
                heading="HTTP Request node",
                content="HTTP Request node отправляет запрос к API. Configure method, headers, and body.",
                score=0.2,
            ),
            EvidenceChunkRecord(
                chunk_id="workflow-overview",
                document_id="n8n-doc",
                document_title="n8n Docs",
                heading="Workflows",
                content="n8n workflows connect services and automate steps.",
                score=0.1,
            ),
        ]
    )
    analysis = QuestionAnalyzer().analyze("как отправить запрос к api в n8n?")
    documents = (DocumentCandidate(document_id="n8n-doc", title="n8n Docs", score=0.9),)

    spans = asyncio.run(EvidenceRetriever(chunk_store=store, workspace_id="ws", min_score=0.0).retrieve(analysis, documents))

    assert "HTTP Request node" in store.query_texts[-1]
    assert "method" in store.query_texts[-1]
    assert "headers" in store.query_texts[-1]
    assert "body" in store.query_texts[-1]
    assert spans[0].evidence_id == "http-request-node"


def test_evidence_retriever_enriches_openrouter_api_key_query() -> None:
    store = FakeChunkStore(
        [
            EvidenceChunkRecord(
                chunk_id="api-key",
                document_id="openrouter-doc",
                document_title="OpenRouter Docs",
                heading="API keys",
                content="Use an API key with base_url and the Authorization Bearer header.",
                score=0.2,
            ),
            EvidenceChunkRecord(
                chunk_id="models",
                document_id="openrouter-doc",
                document_title="OpenRouter Docs",
                heading="Models",
                content="OpenRouter lists models from multiple providers.",
                score=0.1,
            ),
        ]
    )
    analysis = QuestionAnalyzer().analyze("как подключить openrouter api ключ?")
    documents = (DocumentCandidate(document_id="openrouter-doc", title="OpenRouter Docs", score=0.9),)

    spans = asyncio.run(EvidenceRetriever(chunk_store=store, workspace_id="ws", min_score=0.0).retrieve(analysis, documents))

    assert "API key" in store.query_texts[-1]
    assert "base_url" in store.query_texts[-1]
    assert "Authorization" in store.query_texts[-1]
    assert "Bearer" in store.query_texts[-1]
    assert spans[0].evidence_id == "api-key"
