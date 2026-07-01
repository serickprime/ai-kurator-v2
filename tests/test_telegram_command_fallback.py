import asyncio
from types import SimpleNamespace

from app.bot.base_status import BaseStatus
from app.bot.handlers import BotServices, handle_text
from app.service_registry.types import ServiceDocsStatus


class FakeMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.message_id = 42
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        del kwargs
        self.replies.append(text)


class FakeRagPipeline:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def answer(self, question: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        self.calls.append(question)
        return SimpleNamespace(answer="RAG answer", status="answered", sources=(), debug={})


class FakeBaseStatusProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def get_status(self) -> BaseStatus:
        self.calls += 1
        return BaseStatus(active_documents_count=3, active_chunks_count=9)


class FakeServiceStatusProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def list_statuses(self, **kwargs: object) -> tuple[ServiceDocsStatus, ...]:
        self.calls.append(kwargs)
        return (
            ServiceDocsStatus(
                service_id="n8n",
                display_name="n8n",
                aliases=("n8n",),
                docs_source="n8n_docs",
                configured_status="enabled",
                docs_status="indexed",
                active_docs_count=50,
                active_chunks_count=284,
                quality_status="PASS",
                docs_source_configured=True,
            ),
        )


def test_base_status_text_fallback_does_not_call_rag() -> None:
    pipeline = FakeRagPipeline()
    provider = FakeBaseStatusProvider()
    services = BotServices(rag_pipeline=pipeline, base_status_provider=provider)
    message = FakeMessage("/base_status")

    asyncio.run(handle_text(_update(message), _context(services)))

    assert provider.calls == 1
    assert pipeline.calls == []
    assert "Документы: 3 active" in message.replies[-1]


def test_base_status_text_fallback_strips_outer_spaces() -> None:
    pipeline = FakeRagPipeline()
    provider = FakeBaseStatusProvider()
    services = BotServices(rag_pipeline=pipeline, base_status_provider=provider)
    message = FakeMessage(" /base_status ")

    asyncio.run(handle_text(_update(message), _context(services)))

    assert provider.calls == 1
    assert pipeline.calls == []


def test_base_status_text_fallback_ignores_bot_suffix() -> None:
    pipeline = FakeRagPipeline()
    provider = FakeBaseStatusProvider()
    services = BotServices(rag_pipeline=pipeline, base_status_provider=provider)
    message = FakeMessage("/base_status@SomeBot test")

    asyncio.run(handle_text(_update(message), _context(services)))

    assert provider.calls == 1
    assert pipeline.calls == []


def test_services_text_fallback_does_not_call_rag() -> None:
    pipeline = FakeRagPipeline()
    provider = FakeServiceStatusProvider()
    services = BotServices(rag_pipeline=pipeline, service_docs_status_provider=provider)
    message = FakeMessage("/services")

    asyncio.run(handle_text(_update(message), _context(services)))

    assert provider.calls == [{"scan_corpus": True}]
    assert pipeline.calls == []
    assert "n8n" in message.replies[-1]


def test_done_text_fallback_exits_upload_mode() -> None:
    pipeline = FakeRagPipeline()
    services = BotServices(rag_pipeline=pipeline)
    services.state_store.set_mode(7, "upload_material")
    message = FakeMessage("/done")

    asyncio.run(handle_text(_update(message), _context(services)))

    assert services.state_store.get(7).mode == "normal"
    assert pipeline.calls == []


def test_unknown_slash_command_does_not_call_rag() -> None:
    pipeline = FakeRagPipeline()
    services = BotServices(rag_pipeline=pipeline)
    message = FakeMessage("/unknown")

    asyncio.run(handle_text(_update(message), _context(services)))

    assert pipeline.calls == []
    assert "Неизвестная команда" in message.replies[-1]
    assert "/help" in message.replies[-1]


def test_regular_text_still_goes_to_rag() -> None:
    pipeline = FakeRagPipeline()
    services = BotServices(rag_pipeline=pipeline)
    message = FakeMessage("как установить n8n локально?")

    asyncio.run(handle_text(_update(message), _context(services)))

    assert pipeline.calls == ["как установить n8n локально?"]
    assert message.replies[-1] == "RAG answer"


def _context(services: BotServices) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}))


def _update(message: FakeMessage) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=7), callback_query=None)
