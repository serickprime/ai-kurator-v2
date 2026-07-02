import asyncio
from types import SimpleNamespace

from app.bot.features.docs_registry import send_docs_wizard_callback
from app.bot.handlers import BotServices, docs_command, docs_wizard_callback, handle_text, help_command
from app.docs_registry.models import DocsSourceCandidate
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


class FakeCallbackQuery:
    def __init__(self, data: str, *, fail_edit: bool = False) -> None:
        self.data = data
        self.message = FakeMessage("callback-source")
        self.fail_edit = fail_edit
        self.answered = False
        self.edits: list[str] = []
        self.edit_markups: list[object] = []

    async def answer(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.answered = True

    async def edit_message_text(self, text: str, **kwargs: object) -> None:
        if self.fail_edit:
            raise RuntimeError("edit failed")
        self.edits.append(text)
        self.edit_markups.append(kwargs.get("reply_markup"))


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
        raise AssertionError("docs wizard must not crawl")

    async def sync(self) -> None:
        self.mutation_calls.append("sync")
        raise AssertionError("docs wizard must not sync")

    async def index(self) -> None:
        self.mutation_calls.append("index")
        raise AssertionError("docs wizard must not index")

    async def write(self) -> None:
        self.mutation_calls.append("write")
        raise AssertionError("docs wizard must not write")


class FakeActivationService:
    def __init__(self) -> None:
        self.plan_calls: list[str] = []
        self.activate_calls: list[str] = []

    def plan(self, service_id_or_alias: str) -> object:
        self.plan_calls.append(service_id_or_alias)
        raise AssertionError("docs wizard must not build activation plans")

    async def activate(self, service_id_or_alias: str) -> object:
        self.activate_calls.append(service_id_or_alias)
        raise AssertionError("docs wizard must not run activation")


class FakeRagPipeline:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def answer(self, question: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        self.calls.append(question)
        return SimpleNamespace(answer="RAG answer", status="answered", sources=(), debug={})


def test_docs_command_shows_short_dashboard_and_inline_keyboard() -> None:
    provider = FakeDocsStatusProvider((_status("n8n", "n8n", "n8n_docs"),))
    services = BotServices(service_docs_status_provider=provider, owner_ids=(7,))
    message = FakeMessage("/docs")

    asyncio.run(docs_command(_update(message, user_id=7), _context(services)))

    assert provider.calls == [{"scan_corpus": False}]
    assert "Документация сервисов" in message.replies[-1]
    assert "✅ Подключено: 1" in message.replies[-1]
    assert "Помощь по документации" not in message.replies[-1]
    assert message.reply_markups[-1] is not None
    keyboard = message.reply_markups[-1].inline_keyboard
    assert any(button.callback_data == "docs:connected" for row in keyboard for button in row)
    assert any(button.callback_data == "docs:candidates" for row in keyboard for button in row)
    assert any(button.callback_data == "docs:preview_help" for row in keyboard for button in row)
    assert any(button.callback_data == "docs:help" for row in keyboard for button in row)
    assert all(button.callback_data != "docs:openrouter" for row in keyboard for button in row)


def test_connected_callback_shows_connected_docs() -> None:
    provider = FakeDocsStatusProvider(
        (
            _status("n8n", "n8n", "n8n_docs"),
            _status("supabase", "Supabase", "supabase_docs"),
            _status("openrouter", "OpenRouter", "openrouter_docs"),
        )
    )
    query = FakeCallbackQuery("docs:connected")
    services = BotServices(service_docs_status_provider=provider, owner_ids=(7,))

    asyncio.run(docs_wizard_callback(_callback_update(query, user_id=7), _context(services)))

    assert query.answered
    assert "Подключённая документация:" in query.edits[-1]
    assert "✅ OpenRouter — PASS" in query.edits[-1]
    assert provider.mutation_calls == []


def test_connected_callback_explains_quality_fail() -> None:
    provider = FakeDocsStatusProvider(
        (
            _status(
                "telegram_bot_api",
                "Telegram Bot API",
                "telegram_bot_api_docs",
                quality="FAIL",
                notes=("quality gate returned FAIL", "required smoke query missing sendMessage"),
            ),
        )
    )
    query = FakeCallbackQuery("docs:connected")
    services = BotServices(service_docs_status_provider=provider, owner_ids=(7,))

    asyncio.run(docs_wizard_callback(_callback_update(query, user_id=7), _context(services)))

    assert "✅ Telegram Bot API — FAIL: required smoke query missing sendMessage" in query.edits[-1]
    assert "{" not in query.edits[-1]


def test_candidates_callback_hides_already_connected_docs() -> None:
    provider = FakeDocsStatusProvider((_status("openrouter", "OpenRouter", "openrouter_docs"),))
    query = FakeCallbackQuery("docs:candidates")

    asyncio.run(
        send_docs_wizard_callback(
            _callback_update(query, user_id=7),
            status_provider=provider,
            action="candidates",
            is_allowed=True,
            candidate_loader=lambda: (
                _candidate("openrouter", "OpenRouter", "openrouter_docs"),
                _candidate("claude_code", "Claude Code", "claude_code_docs"),
            ),
        )
    )

    assert "➕ Claude Code — claude_code" in query.edits[-1]
    assert "➕ OpenRouter" not in query.edits[-1]


def test_preview_help_callback_shows_examples() -> None:
    provider = FakeDocsStatusProvider(())
    query = FakeCallbackQuery("docs:preview_help")
    services = BotServices(service_docs_status_provider=provider, owner_ids=(7,))

    asyncio.run(docs_wizard_callback(_callback_update(query, user_id=7), _context(services)))

    assert "Проверка кандидата:" in query.edits[-1]
    assert "`/docs_preview ollama`" in query.edits[-1]
    assert "`/docs_preview telegram_bot_api`" in query.edits[-1]
    assert "`/docs_preview claude_code`" in query.edits[-1]
    assert "Это только preview. Документация не подключается." in query.edits[-1]


def test_help_callback_shows_short_instruction() -> None:
    provider = FakeDocsStatusProvider(())
    query = FakeCallbackQuery("docs:help")
    services = BotServices(service_docs_status_provider=provider, owner_ids=(7,))

    asyncio.run(docs_wizard_callback(_callback_update(query, user_id=7), _context(services)))

    assert "- /docs — панель документации" in query.edits[-1]
    assert "- /docs_preview <id> — безопасный предпросмотр" in query.edits[-1]
    assert "- /services — технический статус" in query.edits[-1]


def test_docs_callbacks_do_not_run_activation_confirm_or_rag() -> None:
    provider = FakeDocsStatusProvider((_status("openrouter", "OpenRouter", "openrouter_docs"),))
    activation = FakeActivationService()
    rag = FakeRagPipeline()
    query = FakeCallbackQuery("docs:connected")
    services = BotServices(
        rag_pipeline=rag,
        service_docs_status_provider=provider,
        docs_activation_service=activation,
        owner_ids=(7,),
    )

    asyncio.run(docs_wizard_callback(_callback_update(query, user_id=7), _context(services)))

    assert activation.plan_calls == []
    assert activation.activate_calls == []
    assert rag.calls == []
    assert provider.mutation_calls == []


def test_docs_callback_falls_back_to_reply_when_edit_fails() -> None:
    provider = FakeDocsStatusProvider((_status("n8n", "n8n", "n8n_docs"),))
    query = FakeCallbackQuery("docs:connected", fail_edit=True)
    services = BotServices(service_docs_status_provider=provider, owner_ids=(7,))

    asyncio.run(docs_wizard_callback(_callback_update(query, user_id=7), _context(services)))

    assert query.answered
    assert query.edits == []
    assert "Подключённая документация:" in query.message.replies[-1]


def test_docs_callbacks_all_answer_with_edit_or_reply() -> None:
    provider = FakeDocsStatusProvider((_status("n8n", "n8n", "n8n_docs"),))
    services = BotServices(service_docs_status_provider=provider, owner_ids=(7,))

    for action in ("docs:connected", "docs:candidates", "docs:preview_help", "docs:help"):
        query = FakeCallbackQuery(action)
        asyncio.run(docs_wizard_callback(_callback_update(query, user_id=7), _context(services)))
        assert query.answered
        assert query.edits or query.message.replies


def test_docs_callback_is_denied_to_regular_user() -> None:
    provider = FakeDocsStatusProvider((_status("n8n", "n8n", "n8n_docs"),))
    query = FakeCallbackQuery("docs:connected")
    services = BotServices(service_docs_status_provider=provider, owner_ids=(1,))

    asyncio.run(docs_wizard_callback(_callback_update(query, user_id=7), _context(services)))

    assert provider.calls == []
    assert "Панель документации доступна владельцу бота." in query.edits[-1]


def test_help_command_is_sectioned_and_mentions_docs() -> None:
    message = FakeMessage("/help")

    asyncio.run(help_command(_update(message, user_id=7), _context(BotServices())))

    reply = message.replies[-1]
    assert "Как работать:" in reply
    assert "База знаний:" in reply
    assert "Диагностика:" in reply
    assert "Для владельца:" in reply
    assert "/docs" in reply
    assert "/docs_activate openrouter" in reply


def test_docs_text_fallback_still_does_not_call_rag() -> None:
    provider = FakeDocsStatusProvider((_status("n8n", "n8n", "n8n_docs"),))
    rag = FakeRagPipeline()
    services = BotServices(rag_pipeline=rag, service_docs_status_provider=provider, owner_ids=(7,))
    message = FakeMessage("/docs")

    asyncio.run(handle_text(_update(message, user_id=7), _context(services)))

    assert rag.calls == []
    assert "Документация сервисов" in message.replies[-1]


def test_docs_wizard_messages_do_not_show_raw_json() -> None:
    provider = FakeDocsStatusProvider((_status("n8n", "n8n", "n8n_docs"),))
    query = FakeCallbackQuery("docs:connected")
    services = BotServices(service_docs_status_provider=provider, owner_ids=(7,))

    asyncio.run(docs_wizard_callback(_callback_update(query, user_id=7), _context(services)))

    assert "{" not in query.edits[-1]
    assert "ServiceDocsStatus" not in query.edits[-1]


def _status(
    service_id: str,
    display_name: str,
    docs_source: str | None,
    *,
    docs_status: str = "indexed",
    quality: str | None = None,
    notes: tuple[str, ...] = (),
) -> ServiceDocsStatus:
    quality_status = quality or ("PASS" if docs_status == "indexed" else "none")
    return ServiceDocsStatus(
        service_id=service_id,
        display_name=display_name,
        aliases=(service_id,),
        docs_source=docs_source,
        configured_status="enabled" if docs_source else "not_configured",
        docs_status=docs_status,  # type: ignore[arg-type]
        active_docs_count=5 if docs_status == "indexed" else 0,
        active_chunks_count=25 if docs_status == "indexed" else 0,
        quality_status=quality_status,
        docs_source_configured=bool(docs_source),
        notes=notes,
    )


def _candidate(service_id: str, display_name: str, docs_source: str) -> DocsSourceCandidate:
    return DocsSourceCandidate(
        service_id=service_id,
        display_name=display_name,
        aliases=(service_id,),
        docs_source=docs_source,
        official_start_urls=("https://docs.example.com/",),
        allowed_domains=("docs.example.com",),
        allow_patterns=(r"^https://docs\.example\.com/",),
        deny_patterns=("/login",),
        max_pages=10,
        crawl_depth=1,
        risk_level="low",
        notes="test",
    )


def _context(services: BotServices) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}), args=None)


def _update(message: FakeMessage, *, user_id: int) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id), callback_query=None)


def _callback_update(query: FakeCallbackQuery, *, user_id: int) -> SimpleNamespace:
    return SimpleNamespace(message=None, effective_user=SimpleNamespace(id=user_id), callback_query=query)
