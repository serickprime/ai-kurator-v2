import asyncio
from types import SimpleNamespace

from app.bot.handlers import BotServices, docs_activate_command, docs_command, handle_text, help_command
from app.docs_registry.activation import (
    DocsActivationPlan,
    DocsActivationQualityGate,
    DocsActivationResult,
)
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


class FakeActivationService:
    def __init__(self) -> None:
        self.plan_calls: list[str] = []
        self.activate_calls: list[str] = []
        self.write_calls: list[str] = []

    def plan(self, service_id_or_alias: str) -> DocsActivationPlan:
        self.plan_calls.append(service_id_or_alias)
        return _plan()

    async def activate(self, service_id_or_alias: str) -> DocsActivationResult:
        self.activate_calls.append(service_id_or_alias)
        self.write_calls.append("activate")
        return DocsActivationResult(
            plan=_plan(),
            fetched_pages=5,
            indexed_new=5,
            skipped_unchanged=0,
            archived_old=0,
            failed=0,
            chunks_total=30,
            quality_gate=DocsActivationQualityGate(passed=True),
        )


class FakeRagPipeline:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def answer(self, question: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        self.calls.append(question)
        return SimpleNamespace(answer="RAG answer", status="answered", sources=(), debug={})


class FakeDocsStatusProvider:
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
                active_chunks_count=250,
                quality_status="PASS",
                docs_source_configured=True,
            ),
        )


def test_docs_activate_plan_for_openrouter_does_not_activate() -> None:
    activation = FakeActivationService()
    services = BotServices(docs_activation_service=activation, owner_ids=(7,))
    message = FakeMessage("/docs_activate openrouter")

    asyncio.run(docs_activate_command(_update(message, user_id=7), _context(services)))

    assert activation.plan_calls == ["openrouter"]
    assert activation.activate_calls == []
    assert activation.write_calls == []
    assert "Controlled activation: OpenRouter" in message.replies[-1]
    assert "/docs_activate openrouter confirm" in message.replies[-1]


def test_docs_activate_confirm_for_openrouter_calls_activation_service() -> None:
    activation = FakeActivationService()
    services = BotServices(docs_activation_service=activation, owner_ids=(7,))
    message = FakeMessage("/docs_activate openrouter confirm")

    asyncio.run(docs_activate_command(_update(message, user_id=7), _context(services)))

    assert activation.activate_calls == ["openrouter"]
    assert "Quality gate: PASS" in message.replies[-1]
    assert "Indexed new: 5" in message.replies[-1]


def test_docs_activate_without_argument_asks_for_service() -> None:
    activation = FakeActivationService()
    services = BotServices(docs_activation_service=activation, owner_ids=(7,))
    message = FakeMessage("/docs_activate")

    asyncio.run(docs_activate_command(_update(message, user_id=7), _context(services)))

    assert activation.plan_calls == []
    assert "Укажите сервис: /docs_activate openrouter" in message.replies[-1]


def test_docs_activate_non_owner_is_denied() -> None:
    activation = FakeActivationService()
    services = BotServices(docs_activation_service=activation, owner_ids=(1,))
    message = FakeMessage("/docs_activate openrouter")

    asyncio.run(docs_activate_command(_update(message, user_id=7), _context(services)))

    assert activation.plan_calls == []
    assert "Подключение документации доступно владельцу бота." in message.replies[-1]


def test_docs_activate_missing_runtime_gives_clear_message() -> None:
    services = BotServices(docs_activation_service=None, owner_ids=(7,))
    message = FakeMessage("/docs_activate openrouter confirm")

    asyncio.run(docs_activate_command(_update(message, user_id=7), _context(services)))

    assert "Подключение документации пока недоступно: не хватает runtime-настроек." in message.replies[-1]


def test_docs_activate_text_fallback_does_not_call_rag() -> None:
    activation = FakeActivationService()
    rag = FakeRagPipeline()
    services = BotServices(rag_pipeline=rag, docs_activation_service=activation, owner_ids=(7,))
    message = FakeMessage("/docs_activate openrouter")

    asyncio.run(handle_text(_update(message, user_id=7), _context(services)))

    assert activation.plan_calls == ["openrouter"]
    assert rag.calls == []
    assert "Controlled activation: OpenRouter" in message.replies[-1]


def test_help_mentions_docs_activate() -> None:
    message = FakeMessage()

    asyncio.run(help_command(_update(message, user_id=7), _context(BotServices())))

    assert "/docs_activate openrouter" in message.replies[-1]


def test_docs_dashboard_has_openrouter_wizard_button() -> None:
    provider = FakeDocsStatusProvider()
    services = BotServices(service_docs_status_provider=provider, owner_ids=(7,))
    message = FakeMessage("/docs")

    asyncio.run(docs_command(_update(message, user_id=7), _context(services)))

    assert "{" not in message.replies[-1]
    keyboard = message.reply_markups[-1].inline_keyboard
    assert any(button.callback_data == "docs:openrouter" for row in keyboard for button in row)


def test_docs_activate_messages_do_not_show_raw_json() -> None:
    activation = FakeActivationService()
    services = BotServices(docs_activation_service=activation, owner_ids=(7,))
    message = FakeMessage("/docs_activate openrouter confirm")

    asyncio.run(docs_activate_command(_update(message, user_id=7), _context(services)))

    reply = message.replies[-1]
    assert "{" not in reply
    assert "DocsActivationResult" not in reply
    assert "quality_gate" not in reply


def _plan() -> DocsActivationPlan:
    return DocsActivationPlan(
        service_id="openrouter",
        display_name="OpenRouter",
        docs_source="openrouter_docs",
        allowed_domains=("openrouter.ai",),
        start_urls=("https://openrouter.ai/docs",),
        max_pages=25,
        crawl_depth=2,
        risk_level="low",
        confirm_command="/docs_activate openrouter confirm",
    )


def _context(services: BotServices, *, args: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}), args=args)


def _update(message: FakeMessage, *, user_id: int) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id), callback_query=None)
