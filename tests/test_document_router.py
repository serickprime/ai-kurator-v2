import asyncio

from app.rag.document_router import DocumentCardRecord, DocumentRouter
from app.rag.question_analysis import QuestionAnalyzer
from app.rag.types import DocumentCandidate


class FakeDocumentCardStore:
    def __init__(
        self,
        records: list[DocumentCardRecord],
        vector_records: list[DocumentCardRecord] | None = None,
    ) -> None:
        self.records = records
        self.vector_records = vector_records or []

    async def list_document_cards(
        self,
        *,
        workspace_id: str,
        course: str | None,
        limit: int,
    ) -> list[DocumentCardRecord]:
        del workspace_id, limit
        if course:
            return [record for record in self.records if record.course == course]
        return self.records

    async def match_document_cards(
        self,
        *,
        workspace_id: str,
        query_embedding: list[float],
        course: str | None,
        limit: int,
    ) -> list[DocumentCardRecord]:
        del workspace_id, query_embedding, limit
        if course:
            return [record for record in self.vector_records if record.course == course]
        return self.vector_records


class FakeEmbeddingClient:
    async def embed(self, text: str) -> list[float]:
        del text
        return [0.1] * 1024


def test_document_candidate_carries_routing_reason() -> None:
    candidate = DocumentCandidate(
        document_id="doc-1",
        title="Supabase setup",
        reason="Matches document card keywords",
        score=0.91,
    )

    assert candidate.document_id == "doc-1"
    assert candidate.reason
    assert candidate.score > 0


def test_document_router_selects_answerable_document() -> None:
    analysis = QuestionAnalyzer().analyze("как установить н8н локально?")
    router = DocumentRouter(store=FakeDocumentCardStore(_records()))

    candidates = asyncio.run(router.route(analysis, workspace_id="workspace-1", course="n8n 3.0", limit=3))

    assert candidates
    assert candidates[0].document_id == "install-n8n"
    assert "answerable facets" in candidates[0].reason
    assert "локальная установка" in candidates[0].matched_topics


def test_platform_only_match_is_not_enough() -> None:
    analysis = QuestionAnalyzer().analyze("как установить н8н локально?")
    vector_noise = _yumoney_record(vector_score=0.99)
    router = DocumentRouter(
        store=FakeDocumentCardStore(
            records=_records(),
            vector_records=[vector_noise],
        ),
        embedding_client=FakeEmbeddingClient(),
    )

    candidates = asyncio.run(router.route(analysis, workspace_id="workspace-1", course="n8n 3.0", limit=3))

    assert candidates
    assert candidates[0].document_id == "install-n8n"
    assert all(candidate.document_id != "yumoney-n8n" or candidate.score < candidates[0].score for candidate in candidates)


def _records() -> list[DocumentCardRecord]:
    return [
        DocumentCardRecord(
            document_id="install-n8n",
            filename="01-local-n8n-install.md",
            title="Локальная установка n8n",
            course="n8n 3.0",
            lesson="Установка",
            summary="Материал объясняет локальную установку n8n через Docker или npx, запуск на localhost и проверку порта.",
            topics=("n8n", "локальная установка", "Docker", "localhost"),
            questions_answered=(
                "Как установить n8n локально?",
                "Как открыть интерфейс n8n после запуска?",
                "Как проверить, что локальный сервер n8n работает?",
            ),
            entities=("n8n", "Docker", "localhost"),
            task_types=("setup", "how_to"),
            quality_score=0.92,
        ),
        _yumoney_record(),
        DocumentCardRecord(
            document_id="supabase-api",
            filename="supabase-api.md",
            title="Supabase API в n8n",
            course="n8n 3.0",
            lesson="Supabase",
            summary="Материал про HTTP-запросы к Supabase API из workflow n8n.",
            topics=("Supabase", "API", "n8n"),
            questions_answered=("Как подключить Supabase API в n8n?",),
            entities=("Supabase", "API", "n8n"),
            task_types=("setup", "reference"),
            quality_score=0.87,
        ),
    ]


def _yumoney_record(vector_score: float | None = None) -> DocumentCardRecord:
    return DocumentCardRecord(
        document_id="yumoney-n8n",
        filename="yumoney-in-n8n.md",
        title="ЮMoney в n8n",
        course="n8n 3.0",
        lesson="Платежи",
        summary="Материал про подключение ЮMoney в n8n и настройку платежного workflow.",
        topics=("n8n", "ЮMoney", "платежи"),
        questions_answered=("Как подключить ЮMoney в n8n?",),
        entities=("n8n", "ЮMoney"),
        task_types=("setup", "how_to"),
        quality_score=0.9,
        vector_score=vector_score,
    )
