import asyncio
from types import SimpleNamespace

from app.bot.features.docs_registry import format_docs_dashboard
from app.bot.handlers import BotServices, docs_command, handle_text, help_command
from app.service_registry.types import ServiceDocsStatus


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.message_id = 42
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        del kwargs
        self.replies.append(text)


class FakeDocsStatusProvider:
    def __init__(self, statuses: tuple[ServiceDocsStatus, ...]) -> None:
        self.statuses = statuses
        self.calls: list[dict[str, object]] = []
        self.mutation_calls: list[str] = []

    async def list_statuses(self, **kwargs: object) -> tuple[ServiceDocsStatus, ...]:
        self.calls.append(kwargs)
        return self.statuses

    async def crawl(self) -> None:
        self.mutation_calls.append("crawl")
        raise AssertionError("crawl must not be called by /docs")

    async def sync(self) -> None:
        self.mutation_calls.append("sync")
        raise AssertionError("sync must not be called by /docs")

    async def index(self) -> None:
        self.mutation_calls.append("index")
        raise AssertionError("index must not be called by /docs")

    async def write(self) -> None:
        self.mutation_calls.append("write")
        raise AssertionError("write must not be called by /docs")


class FakeRagPipeline:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def answer(self, question: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        self.calls.append(question)
        return SimpleNamespace(answer="RAG answer", status="answered", sources=(), debug={})


def test_docs_dashboard_shows_connected_sources() -> None:
    text = format_docs_dashboard(
        (
            _status("n8n", "n8n", "n8n_docs", active_docs=50, quality="PASS"),
            _status("supabase", "Supabase", "supabase_docs", active_docs=25, quality="PASS"),
        )
    )

    assert "Документация сервисов:" in text
    assert "Подключено:" in text
    assert "✅ n8n — PASS" in text
    assert "✅ Supabase — PASS" in text


def test_docs_dashboard_shows_not_configured_services() -> None:
    text = format_docs_dashboard(
        (
            _status("flutterflow", "FlutterFlow", None, docs_status="not_configured"),
        )
    )

    assert "Не подключено:" in text
    assert "⚪ FlutterFlow" in text


def test_docs_command_is_read_only_and_does_not_mutate_runtime() -> None:
    provider = FakeDocsStatusProvider((_status("n8n", "n8n", "n8n_docs", active_docs=50),))
    services = BotServices(service_docs_status_provider=provider, owner_ids=(7,))
    message = FakeMessage()

    asyncio.run(docs_command(_update(message, user_id=7), _context(services)))

    assert provider.calls == [{"scan_corpus": False}]
    assert provider.mutation_calls == []
    assert "✅ n8n" in message.replies[-1]


def test_docs_command_does_not_show_json_or_dict_repr() -> None:
    provider = FakeDocsStatusProvider((_status("n8n", "n8n", "n8n_docs", active_docs=50),))
    services = BotServices(service_docs_status_provider=provider, owner_ids=(7,))
    message = FakeMessage()

    asyncio.run(docs_command(_update(message, user_id=7), _context(services)))

    reply = message.replies[-1]
    assert "{" not in reply
    assert "ServiceDocsStatus" not in reply
    assert "docs_status" not in reply


def test_docs_command_is_available_to_admin() -> None:
    provider = FakeDocsStatusProvider((_status("supabase", "Supabase", "supabase_docs", active_docs=25),))
    services = BotServices(service_docs_status_provider=provider, admin_ids=(7,))
    message = FakeMessage()

    asyncio.run(docs_command(_update(message, user_id=7), _context(services)))

    assert provider.calls == [{"scan_corpus": False}]
    assert "✅ Supabase" in message.replies[-1]


def test_docs_command_is_denied_to_regular_user() -> None:
    provider = FakeDocsStatusProvider((_status("n8n", "n8n", "n8n_docs", active_docs=50),))
    services = BotServices(service_docs_status_provider=provider, owner_ids=(1,))
    message = FakeMessage()

    asyncio.run(docs_command(_update(message, user_id=7), _context(services)))

    assert provider.calls == []
    assert "Панель документации доступна владельцу бота." in message.replies[-1]


def test_help_mentions_docs_command() -> None:
    message = FakeMessage()

    asyncio.run(help_command(_update(message, user_id=7), _context(BotServices())))

    assert "/docs" in message.replies[-1]
    assert "панель документации сервисов" in message.replies[-1]


def test_docs_text_fallback_does_not_call_rag() -> None:
    provider = FakeDocsStatusProvider((_status("n8n", "n8n", "n8n_docs", active_docs=50),))
    pipeline = FakeRagPipeline()
    services = BotServices(rag_pipeline=pipeline, service_docs_status_provider=provider, owner_ids=(7,))
    message = FakeMessage("/docs")

    asyncio.run(handle_text(_update(message, user_id=7), _context(services)))

    assert provider.calls == [{"scan_corpus": False}]
    assert pipeline.calls == []
    assert "Документация сервисов:" in message.replies[-1]


def _status(
    service_id: str,
    display_name: str,
    docs_source: str | None,
    *,
    active_docs: int = 1,
    quality: str = "PASS",
    docs_status: str = "indexed",
) -> ServiceDocsStatus:
    return ServiceDocsStatus(
        service_id=service_id,
        display_name=display_name,
        aliases=(service_id,),
        docs_source=docs_source,
        configured_status="enabled" if docs_source else "not_configured",
        docs_status=docs_status,  # type: ignore[arg-type]
        active_docs_count=active_docs,
        active_chunks_count=active_docs * 5,
        quality_status=quality,
        docs_source_configured=bool(docs_source),
    )


def _context(services: BotServices) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}))


def _update(message: FakeMessage, *, user_id: int) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id), callback_query=None)
