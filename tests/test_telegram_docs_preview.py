import asyncio
from types import SimpleNamespace

from app.bot.handlers import BotServices, docs_preview_command, handle_text, help_command
from app.docs_registry.models import DocsCandidatePreviewResult
from app.docs_registry.preview import ArbitraryDocsUrlError, DocsCandidateNotFoundError


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.message_id = 42
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        del kwargs
        self.replies.append(text)


class FakePreviewService:
    def __init__(self, result: DocsCandidatePreviewResult | None = None, error: Exception | None = None) -> None:
        self.result = result or _preview_result()
        self.error = error
        self.calls: list[tuple[str, int]] = []

    async def preview(self, service_id_or_alias: str, *, limit: int = 5) -> DocsCandidatePreviewResult:
        self.calls.append((service_id_or_alias, limit))
        if self.error is not None:
            raise self.error
        return self.result


class FakeRagPipeline:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def answer(self, question: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        self.calls.append(question)
        return SimpleNamespace(answer="RAG answer", status="answered", sources=(), debug={})


def test_docs_preview_is_owner_only() -> None:
    preview = FakePreviewService()
    services = BotServices(docs_preview_service=preview, owner_ids=(1,))
    message = FakeMessage("/docs_preview claude_code")

    asyncio.run(docs_preview_command(_update(message, user_id=7), _context(services)))

    assert preview.calls == []
    assert "Предпросмотр документации доступен владельцу бота." in message.replies[-1]


def test_docs_preview_is_available_to_admin() -> None:
    preview = FakePreviewService()
    services = BotServices(docs_preview_service=preview, admin_ids=(7,))
    message = FakeMessage("/docs_preview claude_code")

    asyncio.run(docs_preview_command(_update(message, user_id=7), _context(services)))

    assert preview.calls == [("claude_code", 5)]
    assert "Предпросмотр документации: Claude Code" in message.replies[-1]


def test_docs_preview_without_argument_asks_for_service_id() -> None:
    preview = FakePreviewService()
    services = BotServices(docs_preview_service=preview, owner_ids=(7,))
    message = FakeMessage("/docs_preview")

    asyncio.run(docs_preview_command(_update(message, user_id=7), _context(services)))

    assert preview.calls == []
    assert "Укажите сервис: /docs_preview claude_code" in message.replies[-1]


def test_docs_preview_unknown_candidate_is_clear() -> None:
    preview = FakePreviewService(error=DocsCandidateNotFoundError("missing"))
    services = BotServices(docs_preview_service=preview, owner_ids=(7,))
    message = FakeMessage("/docs_preview missing")

    asyncio.run(docs_preview_command(_update(message, user_id=7), _context(services)))

    assert "Кандидат документации не найден. Посмотрите список в /docs." in message.replies[-1]


def test_docs_preview_rejects_arbitrary_url() -> None:
    preview = FakePreviewService(error=ArbitraryDocsUrlError("no urls"))
    services = BotServices(docs_preview_service=preview, owner_ids=(7,))
    message = FakeMessage("/docs_preview https://example.com")

    asyncio.run(docs_preview_command(_update(message, user_id=7), _context(services)))

    assert "Произвольные URL нельзя проверять" in message.replies[-1]


def test_docs_preview_does_not_show_raw_json() -> None:
    services = BotServices(docs_preview_service=FakePreviewService(), owner_ids=(7,))
    message = FakeMessage("/docs_preview claude_code")

    asyncio.run(docs_preview_command(_update(message, user_id=7), _context(services)))

    reply = message.replies[-1]
    assert "{" not in reply
    assert "DocsCandidatePreviewResult" not in reply
    assert "sample_urls" not in reply


def test_docs_preview_text_fallback_does_not_call_rag() -> None:
    preview = FakePreviewService()
    pipeline = FakeRagPipeline()
    services = BotServices(rag_pipeline=pipeline, docs_preview_service=preview, owner_ids=(7,))
    message = FakeMessage("/docs_preview claude_code")

    asyncio.run(handle_text(_update(message, user_id=7), _context(services)))

    assert preview.calls == [("claude_code", 5)]
    assert pipeline.calls == []
    assert "Предпросмотр документации: Claude Code" in message.replies[-1]


def test_help_mentions_docs_preview() -> None:
    message = FakeMessage()

    asyncio.run(help_command(_update(message, user_id=7), _context(BotServices())))

    assert "/docs_preview <id>" in message.replies[-1]


def _preview_result() -> DocsCandidatePreviewResult:
    return DocsCandidatePreviewResult(
        service_id="claude_code",
        display_name="Claude Code",
        docs_source="claude_code_docs",
        allowed_domains=("docs.example.com",),
        start_urls=("https://docs.example.com/start",),
        pages_checked=5,
        pages_found=2,
        sample_titles=("Overview", "CLI reference"),
        sample_urls=("https://docs.example.com/start",),
        status="ok",
        warnings=(),
        risk_level="low",
        notes="test",
    )


def _context(services: BotServices) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}), args=None)


def _update(message: FakeMessage, *, user_id: int) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id), callback_query=None)
