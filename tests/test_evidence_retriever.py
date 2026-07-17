import asyncio
from pathlib import Path

import pytest

from app.rag.evidence_pack import EvidencePackBuilder, build_sources
from app.rag.evidence_retriever import (
    EvidenceChunkRecord,
    EvidenceRetriever,
    SupabaseEvidenceChunkStore,
    _discard_reason,
    score_evidence_record,
)
from app.rag.question_analysis import QuestionAnalyzer
from app.rag.query_enrichment import QueryEnricher
from app.rag.reranker import EvidenceReranker
from app.rag.types import DocumentCandidate, QueryEnrichmentContext, QueryFacet, QuestionAnalysis


class CountingQueryEnricher:
    def __init__(self, wrapped: QueryEnricher) -> None:
        self._wrapped = wrapped
        self.build_context_calls = 0

    def build_context(self, question: str):
        self.build_context_calls += 1
        return self._wrapped.build_context(question)


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


class FocusedQueryChunkStore:
    def __init__(
        self,
        *,
        base_records: list[EvidenceChunkRecord],
        focused_records: list[EvidenceChunkRecord],
    ) -> None:
        self.base_records = base_records
        self.focused_records = focused_records
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
        records = self.base_records if len(self.query_texts) == 1 else self.focused_records
        return [record for record in records if record.document_id in allowed]


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


def test_evidence_retriever_keeps_high_signal_evidence_from_routed_documents() -> None:
    store = FakeChunkStore(
        [
            EvidenceChunkRecord(
                chunk_id="overview-1",
                document_id="overview-doc",
                document_title="OpenRouter overview",
                content="OpenRouter provides access to models from multiple providers.",
                score=0.9,
            ),
            EvidenceChunkRecord(
                chunk_id="auth-1",
                document_id="auth-doc",
                document_title="OpenRouter authentication",
                heading="API key authentication",
                content="Configure the OpenRouter API key with the Authorization Bearer header.",
                score=0.7,
            ),
        ]
    )
    analysis = QuestionAnalyzer().analyze("как подключить OpenRouter API ключ?")
    assert analysis.enrichment_context.confirmed_service_ids == ("openrouter",)
    documents = (
        DocumentCandidate(document_id="overview-doc", title="OpenRouter overview", score=0.9),
        DocumentCandidate(document_id="auth-doc", title="OpenRouter authentication", score=0.8),
    )

    retriever = EvidenceRetriever(chunk_store=store, workspace_id="ws")
    spans = asyncio.run(retriever.retrieve(analysis, documents))
    reranked = EvidenceReranker().rerank(spans, analysis=analysis)
    builder = EvidencePackBuilder()
    pack = builder.build(reranked, analysis=analysis)

    assert store.calls[0] == ("overview-doc", "auth-doc")
    assert len(store.calls) <= 1 + 2
    assert set(store.calls[-1]) == {"overview-doc", "auth-doc"}
    assert spans[0].evidence_id == "auth-1"
    assert [item.evidence_id for item in pack.items] == ["auth-1"]
    assert build_sources(pack) == ["OpenRouter authentication — API key authentication"]
    assert any(
        decision.evidence_id == "overview-1" and decision.status != "accepted"
        for decision in builder.last_decisions
    )


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
    pack = EvidencePackBuilder().build(spans, analysis=analysis)

    assert spans == ()
    assert retriever.last_discarded
    assert retriever.last_discarded[0].reason == "missing primary object terms"
    assert pack.answer_mode == "out_of_base"
    assert build_sources(pack) == []


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


def test_service_scoped_anchor_query_finds_high_signal_chunk_in_second_document_for_synthetic_service(
    tmp_path: Path,
) -> None:
    glossary_path = tmp_path / "query_glossary.yaml"
    glossary_path.write_text(
        """example_service:
  display_name: ExampleService
  aliases:
    - ExampleService
  rules:
    - phrases:
        - create widget
      object_anchors:
        - Widget Builder
      config_terms:
        - api_key
        - region
""",
        encoding="utf-8",
    )
    analysis = QuestionAnalyzer(query_enricher=QueryEnricher.from_config(glossary_path, strict=True)).analyze(
        "How do I create widget in ExampleService?"
    )
    assert analysis.enrichment_context.confirmed_service_ids == ("example_service",)
    assert [(anchor.service_id, anchor.term) for anchor in analysis.enrichment_context.glossary_object_anchors] == [
        ("example_service", "Widget Builder")
    ]
    store = FocusedQueryChunkStore(
        base_records=[
            EvidenceChunkRecord(
                chunk_id="overview",
                document_id="overview-doc",
                document_title="General overview",
                content="Automation overview for routine workflows.",
                score=0.8,
            )
        ],
        focused_records=[
            EvidenceChunkRecord(
                chunk_id="widget-builder",
                document_id="reference-doc",
                document_title="Example reference",
                heading="Widget Builder",
                content="Use Widget Builder with api_key and region to create widgets.",
                score=0.7,
            )
        ],
    )
    documents = (
        DocumentCandidate(document_id="overview-doc", title="Example overview", score=0.9),
        DocumentCandidate(document_id="reference-doc", title="Example reference", score=0.8),
    )

    spans = asyncio.run(EvidenceRetriever(chunk_store=store, workspace_id="ws", min_score=0.0).retrieve(analysis, documents))

    assert len(store.calls) <= 1 + 2
    assert all(call == ("overview-doc", "reference-doc") for call in store.calls)
    assert any("Widget Builder" in query and "api_key" in query and "region" in query for query in store.query_texts[1:])
    assert spans[0].evidence_id == "widget-builder"
    assert "overview" not in [span.evidence_id for span in spans]


def test_service_scoped_anchor_query_finds_n8n_official_http_request_chunk() -> None:
    analysis = QuestionAnalyzer().analyze("Как в n8n отправить POST-запрос?")
    assert analysis.enrichment_context.confirmed_service_ids == ("n8n",)
    assert [anchor.term for anchor in analysis.enrichment_context.glossary_object_anchors] == ["HTTP Request node"]
    assert set(analysis.enrichment_context.config_terms) >= {"method", "headers", "body"}
    store = FocusedQueryChunkStore(
        base_records=[
            EvidenceChunkRecord(
                chunk_id="workflow-overview",
                document_id="n8n-official",
                document_title="HTTP Request | Nodes | n8n Docs",
                heading="Workflow overview",
                content="n8n workflows connect services and run steps.",
                score=0.8,
                metadata={"source_kind": "external_docs", "service_ids": ["n8n"]},
            )
        ],
        focused_records=[
            EvidenceChunkRecord(
                chunk_id="http-request-node",
                document_id="n8n-official",
                document_title="HTTP Request | Nodes | n8n Docs",
                heading="HTTP Request node",
                content="Use the HTTP Request node with the POST method, headers, and request body.",
                score=0.7,
                metadata={"source_kind": "external_docs", "service_ids": ["n8n"]},
            )
        ],
    )
    documents = (DocumentCandidate(document_id="n8n-official", title="HTTP Request | Nodes | n8n Docs", score=0.9),)

    spans = asyncio.run(EvidenceRetriever(chunk_store=store, workspace_id="ws", min_score=0.0).retrieve(analysis, documents))

    assert any("HTTP Request node" in query and "method" in query for query in store.query_texts[1:])
    assert "http-request-node" in [span.evidence_id for span in spans]
    selected = {span.evidence_id: span for span in spans}
    assert selected["http-request-node"].metadata["glossary_object_anchors"][0]["term"] == "HTTP Request node"


def test_service_scoped_anchor_query_finds_telegram_send_message_chunk() -> None:
    analysis = QuestionAnalyzer().analyze("Как отправить сообщение через Telegram Bot API?")
    assert analysis.enrichment_context.confirmed_service_ids == ("telegram_bot_api",)
    assert "sendMessage" in analysis.enrichment_context.exact_terms
    assert set(analysis.enrichment_context.config_terms) >= {"chat_id", "text"}
    store = FocusedQueryChunkStore(
        base_records=[
            EvidenceChunkRecord(
                chunk_id="bot-api-overview",
                document_id="telegram-official",
                document_title="Telegram Bot API",
                heading="Telegram Bot API",
                content="The Bot API is an HTTP-based interface for building bots.",
                score=0.8,
                metadata={"source_kind": "external_docs", "service_ids": ["telegram_bot_api"]},
            )
        ],
        focused_records=[
            EvidenceChunkRecord(
                chunk_id="send-message",
                document_id="telegram-official",
                document_title="Telegram Bot API",
                heading="sendMessage",
                content="Use sendMessage to send text messages. Required parameters include chat_id and text.",
                score=0.7,
                metadata={"source_kind": "external_docs", "service_ids": ["telegram_bot_api"]},
            )
        ],
    )
    documents = (DocumentCandidate(document_id="telegram-official", title="Telegram Bot API", score=0.9),)

    spans = asyncio.run(EvidenceRetriever(chunk_store=store, workspace_id="ws", min_score=0.0).retrieve(analysis, documents))

    assert any("sendMessage" in query and "chat_id" in query and "text" in query for query in store.query_texts[1:])
    assert "send-message" in [span.evidence_id for span in spans]


def test_service_scoped_retrieval_preserves_chunk_service_metadata() -> None:
    analysis = QuestionAnalyzer().analyze("Как отправить сообщение через Telegram Bot API?")
    store = FocusedQueryChunkStore(
        base_records=[],
        focused_records=[
            EvidenceChunkRecord(
                chunk_id="uploaded-send-message",
                document_id="uploaded-doc",
                document_title="Telegram Bot API",
                heading="Uploaded note",
                content="Use sendMessage with chat_id and text.",
                score=0.9,
                metadata={"source_kind": "uploaded_or_local"},
            ),
            EvidenceChunkRecord(
                chunk_id="official-send-message",
                document_id="telegram-official",
                document_title="Telegram Bot API",
                heading="sendMessage",
                content="Use sendMessage with chat_id and text.",
                score=0.8,
                metadata={"source_kind": "external_docs", "service_ids": ["telegram_bot_api"]},
            ),
        ],
    )
    documents = (
        DocumentCandidate(document_id="uploaded-doc", title="Telegram Bot API", score=0.9),
        DocumentCandidate(document_id="telegram-official", title="Telegram Bot API", score=0.8),
    )

    spans = asyncio.run(EvidenceRetriever(chunk_store=store, workspace_id="ws", min_score=0.0).retrieve(analysis, documents))

    by_id = {span.evidence_id: span for span in spans}
    assert "uploaded-send-message" in by_id
    assert "official-send-message" in by_id
    assert by_id["uploaded-send-message"].metadata.get("service_ids") is None
    assert by_id["official-send-message"].metadata.get("service_ids") == ["telegram_bot_api"]


def test_service_scoped_anchor_query_preserves_supabase_retrieval() -> None:
    analysis = QuestionAnalyzer().analyze("How does Supabase pgvector vector search use embeddings?")
    assert analysis.enrichment_context.confirmed_service_ids == ("supabase",)
    store = FocusedQueryChunkStore(
        base_records=[
            EvidenceChunkRecord(
                chunk_id="storage-overview",
                document_id="supabase-doc",
                document_title="Storage | Supabase Docs",
                content="Supabase Storage stores and serves files.",
                score=0.8,
            )
        ],
        focused_records=[
            EvidenceChunkRecord(
                chunk_id="vectors",
                document_id="supabase-doc",
                document_title="AI & Vectors | Supabase Docs",
                heading="Vector search",
                content="Supabase uses pgvector with embeddings and match_documents for vector search.",
                score=0.8,
            )
        ],
    )
    documents = (DocumentCandidate(document_id="supabase-doc", title="AI & Vectors | Supabase Docs", score=0.9),)

    spans = asyncio.run(EvidenceRetriever(chunk_store=store, workspace_id="ws", min_score=0.0).retrieve(analysis, documents))

    assert spans[0].evidence_id == "vectors"
    assert any("pgvector" in query and "embeddings" in query for query in store.query_texts)


def test_ambiguous_question_without_confirmed_service_does_not_run_service_scoped_anchor_query() -> None:
    analysis = QuestionAnalyzer().analyze("How do I send a POST request?")
    assert analysis.enrichment_context.confirmed_service_ids == ()
    store = FocusedQueryChunkStore(
        base_records=[
            EvidenceChunkRecord(
                chunk_id="local-request",
                document_id="local-doc",
                document_title="Local material",
                content="Send a POST request from the local exercise.",
                score=0.8,
            )
        ],
        focused_records=[
            EvidenceChunkRecord(
                chunk_id="should-not-be-searched",
                document_id="other-doc",
                document_title="Other",
                content="HTTP Request node method headers body.",
                score=0.9,
            )
        ],
    )
    documents = (
        DocumentCandidate(document_id="local-doc", title="Local material", score=0.9),
        DocumentCandidate(document_id="other-doc", title="Other", score=0.8),
    )

    asyncio.run(EvidenceRetriever(chunk_store=store, workspace_id="ws", min_score=0.0).retrieve(analysis, documents))

    assert store.calls == [("local-doc",)]


def test_service_scoped_anchor_query_deduplicates_base_and_focused_candidates() -> None:
    analysis = QuestionAnalyzer().analyze("Как отправить сообщение через Telegram Bot API?")
    duplicate = EvidenceChunkRecord(
        chunk_id="send-message",
        document_id="telegram-official",
        document_title="Telegram Bot API",
        heading="sendMessage",
        content="Use sendMessage with chat_id and text.",
        score=0.7,
    )
    store = FocusedQueryChunkStore(base_records=[duplicate], focused_records=[duplicate])
    documents = (DocumentCandidate(document_id="telegram-official", title="Telegram Bot API", score=0.9),)

    spans = asyncio.run(EvidenceRetriever(chunk_store=store, workspace_id="ws", min_score=0.0).retrieve(analysis, documents))

    assert [span.evidence_id for span in spans] == ["send-message"]
    assert len(store.calls) <= 1 + 2


def test_config_only_focused_candidate_does_not_bypass_primary_object_gate() -> None:
    analysis = QuestionAnalyzer().analyze("Как в n8n отправить POST-запрос?")
    store = FocusedQueryChunkStore(
        base_records=[],
        focused_records=[
            EvidenceChunkRecord(
                chunk_id="config-only",
                document_id="n8n-official",
                document_title="n8n docs",
                heading="Parameters",
                content="Configure method, headers, and body for workflow steps.",
                score=0.9,
            )
        ],
    )
    documents = (DocumentCandidate(document_id="n8n-official", title="n8n docs", score=0.9),)

    retriever = EvidenceRetriever(chunk_store=store, workspace_id="ws", min_score=0.0)
    spans = asyncio.run(retriever.retrieve(analysis, documents))

    assert spans == ()
    assert retriever.last_discarded[0].chunk_id == "config-only"
    assert retriever.last_discarded[0].reason == "missing primary object terms"


@pytest.mark.parametrize(
    "question",
    (
        "Как отправить HTTP-запрос из n8n?",
        "Как в n8n отправить POST-запрос?",
        "Как отправить HTTP запрос из n8n?",
        "Как отправить HTTP﹣запрос из n8n?",
        "Как отправить HTTP--запрос из n8n?",
    ),
)
def test_evidence_retriever_accepts_only_matched_glossary_object_anchor(question: str) -> None:
    store = FakeChunkStore(
        [
            EvidenceChunkRecord(
                chunk_id="http-request-node",
                document_id="n8n-doc",
                document_title="Automation docs",
                heading="HTTP Request node",
                content="Use the HTTP Request node with the POST method, headers, and request body.",
                score=0.7,
            ),
            EvidenceChunkRecord(
                chunk_id="generic-http-terms",
                document_id="n8n-doc",
                document_title="HTTP Request node",
                heading="Workflow overview",
                content="API HTTP request workflow node concepts are introduced here.",
                score=0.9,
            ),
            EvidenceChunkRecord(
                chunk_id="workflow-overview",
                document_id="n8n-doc",
                document_title="Automation docs",
                heading="Workflows",
                content="Workflows connect services and automate steps.",
                score=0.8,
            ),
        ]
    )
    analysis = QuestionAnalyzer().analyze(question)
    assert analysis.enrichment_context.confirmed_service_ids == ("n8n",)
    assert [(anchor.service_id, anchor.term) for anchor in analysis.enrichment_context.glossary_object_anchors] == [
        ("n8n", "HTTP Request node")
    ]
    documents = (DocumentCandidate(document_id="n8n-doc", title="Automation docs", score=0.9),)

    retriever = EvidenceRetriever(chunk_store=store, workspace_id="ws", min_score=0.0)
    spans = asyncio.run(retriever.retrieve(analysis, documents))

    assert "HTTP Request node" in store.query_texts[-1]
    assert [span.evidence_id for span in spans] == ["http-request-node"]
    matched_anchor = spans[0].metadata["glossary_object_anchors"][0]
    assert matched_anchor["service_id"] == "n8n"
    assert matched_anchor["term"] == "HTTP Request node"
    assert matched_anchor["canonical_term"] == "HTTP Request node"
    expected_variant = "http запрос" if "HTTP" in question else "post запрос"
    assert matched_anchor["matched_variant"] == expected_variant
    assert matched_anchor["rule_id"] == "n8n:rule:1"
    assert matched_anchor["provenance"] == "query_glossary"
    discarded = {item.chunk_id: item.reason for item in retriever.last_discarded}
    assert discarded["generic-http-terms"] == "missing primary object terms"
    assert discarded["workflow-overview"] == "missing primary object terms"


def test_arbitrary_enriched_term_cannot_bypass_primary_object_gate() -> None:
    store = FakeChunkStore(
        [
            EvidenceChunkRecord(
                chunk_id="unrelated-anchor",
                document_id="doc-1",
                document_title="Unrelated guide",
                content="Access Key setup is described here.",
                score=0.9,
            )
        ]
    )
    analysis = QuestionAnalysis(
        original_question="как хранить картофель?",
        object_terms=("картофель",),
        exact_terms=("Access Key",),
        query_facets=(QueryFacet("exact", "Access Key"),),
    )
    documents = (DocumentCandidate(document_id="doc-1", title="Unrelated guide", score=0.9),)

    retriever = EvidenceRetriever(chunk_store=store, workspace_id="ws", min_score=0.0)
    spans = asyncio.run(retriever.retrieve(analysis, documents))

    assert spans == ()
    assert retriever.last_discarded[0].reason == "missing primary object terms"


def test_synthetic_glossary_object_anchor_uses_the_same_retrieval_path(tmp_path: Path) -> None:
    glossary_path = tmp_path / "query_glossary.yaml"
    glossary_path.write_text(
        """example_service:
  display_name: ExampleService
  aliases:
    - ExampleService
  rules:
    - phrases:
        - ключ доступа
      object_anchors:
        - Access Key
""",
        encoding="utf-8",
    )
    enricher = QueryEnricher.from_config(glossary_path, strict=True)
    question = "Как настроить ключ-доступа в ExampleService?"
    analysis = QuestionAnalyzer(query_enricher=enricher).analyze(question)
    assert analysis.enrichment_context.confirmed_service_ids == ("example_service",)
    assert [(anchor.service_id, anchor.term, anchor.rule_id) for anchor in analysis.enrichment_context.glossary_object_anchors] == [
        ("example_service", "Access Key", "example_service:rule:1")
    ]
    store = FakeChunkStore(
        [
            EvidenceChunkRecord(
                chunk_id="access-key",
                document_id="example-doc",
                document_title="Example docs",
                heading="Access Key",
                content="Configure the Access Key in the credentials panel.",
                score=0.7,
            ),
            EvidenceChunkRecord(
                chunk_id="generic-key-terms",
                document_id="example-doc",
                document_title="Example docs",
                content="Access management and key rotation overview.",
                score=0.9,
            ),
        ]
    )
    documents = (DocumentCandidate(document_id="example-doc", title="Example docs", score=0.9),)

    retriever = EvidenceRetriever(
        chunk_store=store,
        workspace_id="ws",
        min_score=0.0,
    )
    spans = asyncio.run(retriever.retrieve(analysis, documents))

    assert [span.evidence_id for span in spans] == ["access-key"]
    assert spans[0].metadata["glossary_object_anchors"][0]["service_id"] == "example_service"
    assert any(
        item.chunk_id == "generic-key-terms" and item.reason == "missing primary object terms"
        for item in retriever.last_discarded
    )


def test_direct_helpers_use_analysis_enrichment_context_when_not_explicit(tmp_path: Path) -> None:
    glossary_path = tmp_path / "query_glossary.yaml"
    glossary_path.write_text(
        """example_service:
  display_name: ExampleService
  aliases:
    - ExampleService
  rules:
    - phrases:
        - ключ доступа
      object_anchors:
        - Access Key
""",
        encoding="utf-8",
    )
    enricher = QueryEnricher.from_config(glossary_path, strict=True)
    analysis = QuestionAnalyzer(query_enricher=enricher).analyze(
        "Как настроить ключ-доступа в ExampleService?"
    )
    record = EvidenceChunkRecord(
        chunk_id="access-key",
        document_id="example-doc",
        document_title="Example docs",
        heading="Access Key",
        content="Configure the Access Key in the credentials panel.",
        score=0.7,
    )

    scored = score_evidence_record(analysis, record)

    matched_anchor = scored.metadata["glossary_object_anchors"][0]
    assert matched_anchor == {
        "service_id": "example_service",
        "term": "Access Key",
        "canonical_term": "Access Key",
        "matched_variant": "ключ доступа",
        "rule_id": "example_service:rule:1",
        "provenance": "query_glossary",
    }
    assert scored.score_breakdown["object_match"] == 1.0
    assert _discard_reason(analysis, scored, 0.0) == ""

    explicit_empty_context = QueryEnrichmentContext()
    overridden = score_evidence_record(
        analysis,
        record,
        enrichment_context=explicit_empty_context,
    )

    assert overridden.metadata["glossary_object_anchors"] == []
    assert (
        _discard_reason(
            analysis,
            overridden,
            0.0,
            enrichment_context=explicit_empty_context,
        )
        == "missing primary object terms"
    )


def test_typed_context_is_created_once_and_retriever_does_not_re_enrich() -> None:
    enricher = CountingQueryEnricher(QueryEnricher.default())
    analysis = QuestionAnalyzer(query_enricher=enricher).analyze("Как отправить HTTP-запрос из n8n?")
    store = FakeChunkStore(
        [
            EvidenceChunkRecord(
                chunk_id="http-request-node",
                document_id="n8n-doc",
                document_title="n8n docs",
                heading="HTTP Request node",
                content="Use the HTTP Request node with method, headers, and body.",
                score=0.7,
            )
        ]
    )
    documents = (DocumentCandidate(document_id="n8n-doc", title="n8n docs", score=0.9),)

    spans = asyncio.run(EvidenceRetriever(chunk_store=store, workspace_id="ws", min_score=0.0).retrieve(analysis, documents))

    assert spans
    assert enricher.build_context_calls == 1


def test_alphanumeric_platform_like_token_is_not_confirmed_service_context() -> None:
    analysis = QuestionAnalyzer().analyze("Как настроить API ключ для x9service?")
    assert "x9service" in analysis.platform_terms
    assert analysis.enrichment_context.confirmed_service_ids == ()
    store = FakeChunkStore(
        [
            EvidenceChunkRecord(
                chunk_id="first",
                document_id="first-doc",
                document_title="First",
                content="API key setup for x9service.",
                score=0.8,
            ),
            EvidenceChunkRecord(
                chunk_id="second",
                document_id="second-doc",
                document_title="Second",
                content="API key setup for x9service.",
                score=0.9,
            ),
        ]
    )
    documents = (
        DocumentCandidate(document_id="first-doc", title="First", score=0.8),
        DocumentCandidate(document_id="second-doc", title="Second", score=0.7),
    )

    asyncio.run(EvidenceRetriever(chunk_store=store, workspace_id="ws", min_score=0.0).retrieve(analysis, documents))

    assert store.calls == [("first-doc",)]


def test_glossary_anchor_only_in_document_title_does_not_pass_object_gate() -> None:
    analysis = QuestionAnalyzer().analyze("Как отправить HTTP-запрос из n8n?")
    store = FakeChunkStore(
        [
            EvidenceChunkRecord(
                chunk_id="title-only",
                document_id="n8n-doc",
                document_title="HTTP Request node",
                heading="Overview",
                content="API HTTP request workflow node concepts are introduced here.",
                score=0.9,
            )
        ]
    )
    documents = (DocumentCandidate(document_id="n8n-doc", title="HTTP Request node", score=0.9),)

    retriever = EvidenceRetriever(chunk_store=store, workspace_id="ws", min_score=0.0)
    spans = asyncio.run(retriever.retrieve(analysis, documents))

    assert spans == ()
    assert retriever.last_discarded[0].reason == "missing primary object terms"


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
