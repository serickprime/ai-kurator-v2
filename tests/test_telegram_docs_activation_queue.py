import asyncio
from types import SimpleNamespace

from app.bot.handlers import (
    BotServices,
    docs_activate_ready_command,
    docs_preview_all_command,
    docs_ready_command,
    docs_wizard_callback,
    handle_text,
    help_command,
)
from app.docs_registry.activation import DocsActivationPlan, DocsActivationQualityGate, DocsActivationResult
from app.docs_registry.models import DocsSourceCandidate
from app.docs_registry.queue import DocsQueueActivationResult, DocsQueueItem, DocsQueueReport


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.message_id = 42
        self.replies: list[str] = []
        self.reply_markups: list[object] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        self.replies.append(text)
        self.reply_markups.append(kwargs.get("reply_markup"))


class FakeCallbackQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.message = FakeMessage("callback")
        self.answered = False
        self.edits: list[str] = []

    async def answer(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.answered = True

    async def edit_message_text(self, text: str, **kwargs: object) -> None:
        del kwargs
        self.edits.append(text)


class FakeQueueService:
    def __init__(self, report: DocsQueueReport | None = None) -> None:
        self.report = report or _report()
        self.calls: list[str] = []
        self.activation_calls: list[str] = []

    async def preview_all(self) -> DocsQueueReport:
        self.calls.append("preview_all")
        return self.report

    async def ready(self) -> DocsQueueReport:
        self.calls.append("ready")
        return self.report

    async def activation_plan(self) -> DocsQueueReport:
        self.calls.append("activation_plan")
        return self.report

    async def activate_ready(self) -> DocsQueueActivationResult:
        self.calls.append("activate_ready")
        self.activation_calls.append("activate_ready")
        return DocsQueueActivationResult(
            report=self.report,
            activated=(
                _activation_result("openrouter", "OpenRouter"),
                _activation_result("telegram_bot_api", "Telegram Bot API"),
            ),
            skipped=tuple(item for item in self.report.items if item.status != "ready"),
        )


def test_docs_preview_all_command_shows_batch_report_without_activation() -> None:
    queue = FakeQueueService()
    message = FakeMessage("/docs_preview_all")

    asyncio.run(docs_preview_all_command(_update(message, user_id=7), _context(BotServices(docs_queue_service=queue, owner_ids=(7,)))))

    assert queue.calls == ["preview_all"]
    assert queue.activation_calls == []
    assert "Готовы к подключению:" in message.replies[-1]
    assert "✅ Telegram Bot API — 5 pages" in message.replies[-1]
    assert "✅ OpenRouter — already connected" in message.replies[-1]
    assert "⚠️ Ollama — risk review" in message.replies[-1]
    assert "❌ Claude Code — redirect error" in message.replies[-1]
    assert "{" not in message.replies[-1]


def test_docs_ready_command_shows_only_ready_allowlisted_candidates() -> None:
    queue = FakeQueueService()
    message = FakeMessage("/docs_ready")

    asyncio.run(docs_ready_command(_update(message, user_id=7), _context(BotServices(docs_queue_service=queue, owner_ids=(7,)))))

    assert queue.calls == ["ready"]
    assert "✅ Telegram Bot API — telegram_bot_api" in message.replies[-1]
    assert "OpenRouter — already connected" not in message.replies[-1]
    assert "Ollama" not in message.replies[-1]


def test_docs_activate_ready_plan_does_not_write() -> None:
    queue = FakeQueueService()
    message = FakeMessage("/docs_activate_ready")

    asyncio.run(
        docs_activate_ready_command(_update(message, user_id=7), _context(BotServices(docs_queue_service=queue, owner_ids=(7,))))
    )

    assert queue.calls == ["activation_plan"]
    assert queue.activation_calls == []
    assert "`/docs_activate_ready confirm`" in message.replies[-1]
    assert "Telegram Bot API" in message.replies[-1]
    assert "Ollama" in message.replies[-1]


def test_docs_activate_ready_confirm_calls_queue_activation() -> None:
    queue = FakeQueueService()
    message = FakeMessage("/docs_activate_ready confirm")

    asyncio.run(
        docs_activate_ready_command(_update(message, user_id=7), _context(BotServices(docs_queue_service=queue, owner_ids=(7,))))
    )

    assert queue.calls == ["activate_ready"]
    assert queue.activation_calls == ["activate_ready"]
    assert "Activation queue result" in message.replies[-1]
    assert "✅ OpenRouter — PASS" in message.replies[-1]
    assert "✅ Telegram Bot API — PASS" in message.replies[-1]


def test_docs_activate_ready_rejects_arbitrary_url_argument() -> None:
    queue = FakeQueueService()
    message = FakeMessage("/docs_activate_ready https://example.com")

    asyncio.run(
        docs_activate_ready_command(_update(message, user_id=7), _context(BotServices(docs_queue_service=queue, owner_ids=(7,))))
    )

    assert queue.calls == []
    assert "не принимает URL" in message.replies[-1]


def test_docs_activate_ready_rejects_confirm_with_extra_argument() -> None:
    queue = FakeQueueService()
    message = FakeMessage("/docs_activate_ready confirm https://example.com")

    asyncio.run(
        docs_activate_ready_command(_update(message, user_id=7), _context(BotServices(docs_queue_service=queue, owner_ids=(7,))))
    )

    assert queue.calls == []
    assert queue.activation_calls == []
    assert "URL" in message.replies[-1]
    assert "/docs_activate_ready" in message.replies[-1]


def test_docs_queue_commands_are_owner_only() -> None:
    queue = FakeQueueService()
    message = FakeMessage("/docs_preview_all")

    asyncio.run(docs_preview_all_command(_update(message, user_id=7), _context(BotServices(docs_queue_service=queue, owner_ids=(1,)))))

    assert queue.calls == []
    assert "доступна владельцу" in message.replies[-1]


def test_docs_queue_text_fallback_does_not_call_rag() -> None:
    queue = FakeQueueService()
    rag = SimpleNamespace(calls=[])
    message = FakeMessage("/docs_ready")

    asyncio.run(handle_text(_update(message, user_id=7), _context(BotServices(docs_queue_service=queue, rag_pipeline=rag, owner_ids=(7,)))))

    assert queue.calls == ["ready"]
    assert rag.calls == []


def test_docs_queue_callbacks_do_not_activate_confirm() -> None:
    queue = FakeQueueService()
    services = BotServices(docs_queue_service=queue, owner_ids=(7,))

    for callback in ("docs:preview_all", "docs:ready"):
        query = FakeCallbackQuery(callback)
        asyncio.run(docs_wizard_callback(_callback_update(query, user_id=7), _context(services)))
        assert query.answered
        assert query.edits

    assert queue.calls == ["preview_all", "ready"]
    assert queue.activation_calls == []


def test_help_mentions_docs_queue_commands() -> None:
    message = FakeMessage("/help")

    asyncio.run(help_command(_update(message, user_id=7), _context(BotServices())))

    assert "/docs_preview_all" in message.replies[-1]
    assert "/docs_ready" in message.replies[-1]
    assert "/docs_activate_ready" in message.replies[-1]


def _report() -> DocsQueueReport:
    return DocsQueueReport(
        items=(
            _item("telegram_bot_api", "Telegram Bot API", "telegram_bot_api_docs", "ready", pages_found=5),
            _item("openrouter", "OpenRouter", "openrouter_docs", "already_connected", reason="already connected"),
            _item("ollama", "Ollama", "ollama_docs", "needs_review", reason="risk review", pages_found=5),
            _item("claude_code", "Claude Code", "claude_code_docs", "failed", reason="redirect error"),
        )
    )


def _item(
    service_id: str,
    display_name: str,
    docs_source: str,
    status: str,
    *,
    reason: str = "5 pages",
    pages_found: int = 0,
) -> DocsQueueItem:
    return DocsQueueItem(
        candidate=DocsSourceCandidate(
            service_id=service_id,
            display_name=display_name,
            aliases=(service_id,),
            docs_source=docs_source,
            official_start_urls=("https://docs.example.com/",),
            allowed_domains=("docs.example.com",),
            allow_patterns=(r"^https://docs\.example\.com/",),
            deny_patterns=("/login",),
            max_pages=5,
            crawl_depth=1,
            risk_level="low",
            notes="test",
        ),
        status=status,  # type: ignore[arg-type]
        reason=reason,
        pages_found=pages_found,
        pages_checked=5,
    )


def _activation_result(service_id: str, display_name: str) -> DocsActivationResult:
    return DocsActivationResult(
        plan=DocsActivationPlan(
            service_id=service_id,
            display_name=display_name,
            docs_source=f"{service_id}_docs",
            allowed_domains=("docs.example.com",),
            start_urls=("https://docs.example.com/",),
            max_pages=5,
            crawl_depth=1,
            risk_level="low",
            confirm_command=f"/docs_activate {service_id} confirm",
        ),
        fetched_pages=5,
        indexed_new=5,
        chunks_total=10,
        quality_gate=DocsActivationQualityGate(passed=True),
    )


def _context(services: BotServices) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}), args=None)


def _update(message: FakeMessage, *, user_id: int) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id), callback_query=None)


def _callback_update(query: FakeCallbackQuery, *, user_id: int) -> SimpleNamespace:
    return SimpleNamespace(message=None, effective_user=SimpleNamespace(id=user_id), callback_query=query)
