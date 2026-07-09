import asyncio
from types import SimpleNamespace

import app.bot.handlers as handlers
from app.bot.handlers import BotServices, handle_text, help_command, service_suggest_command
from app.service_registry.types import ServiceDocsStatus


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.message_id = 42
        self.replies: list[str] = []
        self.reply_markups: list[object] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        self.replies.append(text)
        self.reply_markups.append(kwargs.get("reply_markup"))


class FakeStatusProvider:
    def __init__(
        self,
        statuses: tuple[ServiceDocsStatus, ...] = (),
        *,
        error: Exception | None = None,
    ) -> None:
        self.statuses = statuses
        self.error = error
        self.calls: list[dict[str, object]] = []
        self.mutation_calls: list[str] = []

    async def list_statuses(self, **kwargs: object) -> tuple[ServiceDocsStatus, ...]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.statuses

    async def activate(self) -> None:
        self.mutation_calls.append("activate")
        raise AssertionError("activation must not be called by /service_suggest")

    async def crawl(self) -> None:
        self.mutation_calls.append("crawl")
        raise AssertionError("crawl must not be called by /service_suggest")

    async def sync(self) -> None:
        self.mutation_calls.append("sync")
        raise AssertionError("sync must not be called by /service_suggest")

    async def index(self) -> None:
        self.mutation_calls.append("index")
        raise AssertionError("index must not be called by /service_suggest")

    async def reindex(self) -> None:
        self.mutation_calls.append("reindex")
        raise AssertionError("reindex must not be called by /service_suggest")

    async def write(self) -> None:
        self.mutation_calls.append("write")
        raise AssertionError("Supabase writes must not be called by /service_suggest")


class FakeRagPipeline:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def answer(self, question: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        self.calls.append(question)
        return SimpleNamespace(answer="RAG answer", status="answered", sources=(), debug={})


def test_service_suggest_owner_preview_for_stripe_keeps_n8n_context() -> None:
    provider = FakeStatusProvider((_status("n8n", "n8n", "n8n_docs", active_docs=50),))
    services = BotServices(service_docs_status_provider=provider, owner_ids=(7,))
    message = FakeMessage("/service_suggest how to connect Stripe in n8n")

    asyncio.run(service_suggest_command(_update(message, user_id=7), _context(services)))

    reply = message.replies[-1]
    assert provider.calls == [{"scan_corpus": False}]
    assert provider.mutation_calls == []
    assert "Service suggestion preview" in reply
    assert "Detected service: Stripe" in reply
    assert "Service ID: stripe" in reply
    assert "Active context: n8n" in reply
    assert "Current status: known-docs-missing" in reply
    assert "Owner review required: yes" in reply
    assert "Auto activation: disabled" in reply


def test_service_suggest_admin_preview_for_supported_active_service() -> None:
    provider = FakeStatusProvider(
        (_status("telegram_bot_api", "Telegram Bot API", "telegram_bot_api_docs", active_docs=20),)
    )
    services = BotServices(service_docs_status_provider=provider, admin_ids=(7,))
    message = FakeMessage("/service_suggest how to send message through Telegram Bot API")

    asyncio.run(service_suggest_command(_update(message, user_id=7), _context(services)))

    reply = message.replies[-1]
    assert provider.calls == [{"scan_corpus": False}]
    assert "Service ID: telegram_bot_api" in reply
    assert "Current status: supported-active" in reply
    assert "Owner review required: no" in reply
    assert "Owner action: not required; use the regular RAG flow." in reply
    assert "Auto activation: disabled" in reply


def test_service_suggest_unknown_service_has_no_confident_action() -> None:
    provider = FakeStatusProvider()
    services = BotServices(service_docs_status_provider=provider, owner_ids=(7,))
    message = FakeMessage("/service_suggest how to work with some new service")

    asyncio.run(service_suggest_command(_update(message, user_id=7), _context(services)))

    reply = message.replies[-1]
    assert "Detected service: none" in reply
    assert "Service ID: none" in reply
    assert "Confidence: 0.00" in reply
    assert "Current status: unknown-service" in reply
    assert "Suggested next action: no_action" in reply
    assert "Auto activation: disabled" in reply


def test_service_suggest_unauthorized_user_does_not_get_technical_preview() -> None:
    provider = FakeStatusProvider((_status("n8n", "n8n", "n8n_docs", active_docs=50),))
    services = BotServices(service_docs_status_provider=provider, owner_ids=(1,))
    message = FakeMessage("/service_suggest how to connect Stripe in n8n")

    asyncio.run(service_suggest_command(_update(message, user_id=7), _context(services)))

    reply = message.replies[-1]
    assert provider.calls == []
    assert "Service suggestion preview" not in reply
    assert "Detected service" not in reply
    assert "available to the bot owner/admin" in reply


def test_service_suggest_empty_command_returns_usage() -> None:
    provider = FakeStatusProvider()
    services = BotServices(service_docs_status_provider=provider, owner_ids=(7,))
    message = FakeMessage("/service_suggest")

    asyncio.run(service_suggest_command(_update(message, user_id=7), _context(services)))

    assert provider.calls == []
    assert "Usage: /service_suggest" in message.replies[-1]


def test_service_suggest_runtime_unavailable_is_readable_without_traceback() -> None:
    provider = FakeStatusProvider(error=RuntimeError("database unavailable"))
    services = BotServices(service_docs_status_provider=provider, owner_ids=(7,))
    message = FakeMessage("/service_suggest how to connect Stripe in n8n")

    asyncio.run(service_suggest_command(_update(message, user_id=7), _context(services)))

    reply = message.replies[-1]
    assert provider.calls == [{"scan_corpus": False}]
    assert "Runtime status: unavailable: database unavailable" in reply
    assert "Docs availability check: not verified" in reply
    assert "Traceback" not in reply
    assert "Auto activation: disabled" in reply
    assert provider.mutation_calls == []


def test_service_suggest_text_fallback_does_not_call_rag() -> None:
    provider = FakeStatusProvider((_status("n8n", "n8n", "n8n_docs", active_docs=50),))
    pipeline = FakeRagPipeline()
    services = BotServices(rag_pipeline=pipeline, service_docs_status_provider=provider, owner_ids=(7,))
    message = FakeMessage("/service_suggest how to connect Stripe in n8n")

    asyncio.run(handle_text(_update(message, user_id=7), _context(services)))

    assert provider.calls == [{"scan_corpus": False}]
    assert pipeline.calls == []
    assert "Service ID: stripe" in message.replies[-1]


def test_regular_user_question_does_not_trigger_service_suggestion_preview() -> None:
    provider = FakeStatusProvider()
    pipeline = FakeRagPipeline()
    services = BotServices(rag_pipeline=pipeline, service_docs_status_provider=provider, owner_ids=(7,))
    message = FakeMessage("how to connect Stripe in n8n")

    asyncio.run(handle_text(_update(message, user_id=7), _context(services)))

    assert provider.calls == []
    assert pipeline.calls == ["how to connect Stripe in n8n"]
    assert message.replies[-1] == "RAG answer"


def test_service_suggest_handler_delegates_to_feature_layer(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, object]] = []

    async def fake_send(update: object, **kwargs: object) -> None:
        calls.append(kwargs)
        message = getattr(update, "message")
        await message.reply_text("fake preview")

    monkeypatch.setattr(handlers, "send_service_suggestion_preview", fake_send)

    provider = FakeStatusProvider()
    services = BotServices(service_docs_status_provider=provider, owner_ids=(7,))
    message = FakeMessage("/service_suggest how to connect Stripe in n8n")

    asyncio.run(handlers.service_suggest_command(_update(message, user_id=7), _context(services)))

    assert message.replies == ["fake preview"]
    assert calls[0]["question"] == "how to connect Stripe in n8n"
    assert calls[0]["is_allowed"] is True
    assert calls[0]["status_provider"] is provider


def test_help_mentions_service_suggest() -> None:
    message = FakeMessage()

    asyncio.run(help_command(_update(message, user_id=7), _context(BotServices())))

    assert "/service_suggest <question>" in message.replies[-1]


def _status(
    service_id: str,
    display_name: str,
    docs_source: str | None,
    *,
    active_docs: int = 1,
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
        quality_status="PASS" if active_docs else "none",
        docs_source_configured=bool(docs_source),
    )


def _context(services: BotServices) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}), args=None)


def _update(message: FakeMessage, *, user_id: int) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id), callback_query=None)
