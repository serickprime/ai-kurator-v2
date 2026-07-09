import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import app.bot.handlers as handlers
from app.bot.handlers import BotServices, docs_health_command, handle_text, help_command
from app.service_registry.docs_health import DocsHealthReport, DocsSourceHealth


NOW = datetime(2026, 7, 9, tzinfo=timezone.utc)


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.message_id = 42
        self.replies: list[str] = []
        self.reply_markups: list[object] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        self.replies.append(text)
        self.reply_markups.append(kwargs.get("reply_markup"))


class FakeReportProvider:
    def __init__(self, report: DocsHealthReport | None = None, *, error: Exception | None = None) -> None:
        self.report = report or _report()
        self.error = error
        self.calls = 0
        self.mutation_calls: list[str] = []

    async def build_report(self) -> DocsHealthReport:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.report

    async def activate(self) -> None:
        self.mutation_calls.append("activate")
        raise AssertionError("activation must not be called by /docs_health")

    async def crawl(self) -> None:
        self.mutation_calls.append("crawl")
        raise AssertionError("crawl must not be called by /docs_health")

    async def sync(self) -> None:
        self.mutation_calls.append("sync")
        raise AssertionError("sync must not be called by /docs_health")

    async def index(self) -> None:
        self.mutation_calls.append("index")
        raise AssertionError("index must not be called by /docs_health")

    async def reindex(self) -> None:
        self.mutation_calls.append("reindex")
        raise AssertionError("reindex must not be called by /docs_health")

    async def write(self) -> None:
        self.mutation_calls.append("write")
        raise AssertionError("Supabase writes must not be called by /docs_health")


class FakeRagPipeline:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def answer(self, question: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        self.calls.append(question)
        return SimpleNamespace(answer="RAG answer", status="answered", sources=(), debug={})


def test_docs_health_owner_general_report_is_telegram_friendly() -> None:
    provider = FakeReportProvider()
    services = BotServices(docs_health_report_provider=provider, owner_ids=(7,))
    message = FakeMessage("/docs_health")

    asyncio.run(docs_health_command(_update(message, user_id=7), _context(services)))

    reply = message.replies[-1]
    assert provider.calls == 1
    assert "Docs Health" in reply
    assert "Total: 5" in reply
    assert "Healthy: 2" in reply
    assert "Warning: 1" in reply
    assert "Failed: 1" in reply
    assert "Inactive: 1" in reply
    assert "Automatic refresh: disabled" in reply
    assert "Traceback" not in reply
    assert "DocsSourceHealth" not in reply
    assert provider.mutation_calls == []


def test_docs_health_openrouter_filter_shows_warning_reason_and_fresh_stale_status() -> None:
    provider = FakeReportProvider()
    services = BotServices(docs_health_report_provider=provider, owner_ids=(7,))
    message = FakeMessage("/docs_health openrouter")

    asyncio.run(docs_health_command(_update(message, user_id=7), _context(services)))

    reply = message.replies[-1]
    assert "Filter: service=openrouter" in reply
    assert "Service: OpenRouter (openrouter)" in reply
    assert "Service: Telegram Bot API" not in reply
    assert "Status: warning" in reply
    assert "Stale: fresh" in reply
    assert "generator boilerplate found" in reply
    assert "Automatic refresh: disabled" in reply


def test_docs_health_telegram_filter_shows_failed_quality_reasons() -> None:
    provider = FakeReportProvider()
    services = BotServices(docs_health_report_provider=provider, owner_ids=(7,))
    message = FakeMessage("/docs_health telegram_bot_api")

    asyncio.run(docs_health_command(_update(message, user_id=7), _context(services)))

    reply = message.replies[-1]
    assert "Service: Telegram Bot API (telegram_bot_api)" in reply
    assert "Service: OpenRouter" not in reply
    assert "Status: failed" in reply
    assert "Stale: fresh" in reply
    assert "raw HTML markers" in reply
    assert "navigation/footer/cookie markers" in reply


def test_docs_health_healthy_source_has_no_owner_action_required() -> None:
    provider = FakeReportProvider()
    services = BotServices(docs_health_report_provider=provider, owner_ids=(7,))
    message = FakeMessage("/docs_health n8n")

    asyncio.run(docs_health_command(_update(message, user_id=7), _context(services)))

    reply = message.replies[-1]
    assert "Status: healthy" in reply
    assert "Owner action: no owner action required" in reply
    assert "Automatic refresh: disabled" in reply


def test_docs_health_inactive_source_is_not_healthy_or_auto_activated() -> None:
    provider = FakeReportProvider()
    services = BotServices(docs_health_report_provider=provider, owner_ids=(7,))
    message = FakeMessage("/docs_health flutterflow")

    asyncio.run(docs_health_command(_update(message, user_id=7), _context(services)))

    reply = message.replies[-1]
    assert "Status: inactive" in reply
    assert "Status: healthy" not in reply
    assert "Automatic refresh: disabled" in reply
    assert "activation" not in provider.mutation_calls


def test_docs_health_runtime_unavailable_is_readable_without_traceback() -> None:
    provider = FakeReportProvider(error=RuntimeError("database unavailable"))
    services = BotServices(docs_health_report_provider=provider, owner_ids=(7,))
    message = FakeMessage("/docs_health")

    asyncio.run(docs_health_command(_update(message, user_id=7), _context(services)))

    reply = message.replies[-1]
    assert provider.calls == 1
    assert "Runtime: unavailable: database unavailable" in reply
    assert "Traceback" not in reply
    assert "Automatic refresh: disabled" in reply


def test_docs_health_unknown_service_filter_returns_clear_message() -> None:
    provider = FakeReportProvider()
    services = BotServices(docs_health_report_provider=provider, owner_ids=(7,))
    message = FakeMessage("/docs_health missing_service")

    asyncio.run(docs_health_command(_update(message, user_id=7), _context(services)))

    reply = message.replies[-1]
    assert "No matching docs source for: missing_service" in reply
    assert "Available service IDs:" in reply
    assert "openrouter" in reply
    assert "Automatic refresh: disabled" in reply


def test_docs_health_unauthorized_user_does_not_get_report_or_call_provider() -> None:
    provider = FakeReportProvider()
    services = BotServices(docs_health_report_provider=provider, owner_ids=(1,))
    message = FakeMessage("/docs_health openrouter")

    asyncio.run(docs_health_command(_update(message, user_id=7), _context(services)))

    reply = message.replies[-1]
    assert provider.calls == 0
    assert "Docs Health" not in reply
    assert "Service:" not in reply
    assert "available to the bot owner/admin" in reply


def test_docs_health_text_fallback_does_not_call_rag() -> None:
    provider = FakeReportProvider()
    pipeline = FakeRagPipeline()
    services = BotServices(rag_pipeline=pipeline, docs_health_report_provider=provider, owner_ids=(7,))
    message = FakeMessage("/docs_health openrouter")

    asyncio.run(handle_text(_update(message, user_id=7), _context(services)))

    assert provider.calls == 1
    assert pipeline.calls == []
    assert "Service: OpenRouter (openrouter)" in message.replies[-1]


def test_regular_user_message_still_uses_rag_flow() -> None:
    provider = FakeReportProvider()
    pipeline = FakeRagPipeline()
    services = BotServices(rag_pipeline=pipeline, docs_health_report_provider=provider, owner_ids=(7,))
    message = FakeMessage("how to connect OpenRouter API")

    asyncio.run(handle_text(_update(message, user_id=7), _context(services)))

    assert provider.calls == 0
    assert pipeline.calls == ["how to connect OpenRouter API"]
    assert message.replies[-1] == "RAG answer"


def test_docs_health_handler_delegates_to_feature_layer(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, object]] = []

    async def fake_send(update: object, **kwargs: object) -> None:
        calls.append(kwargs)
        message = getattr(update, "message")
        await message.reply_text("fake docs health preview")

    monkeypatch.setattr(handlers, "send_docs_health_preview", fake_send)

    provider = FakeReportProvider()
    services = BotServices(docs_health_report_provider=provider, owner_ids=(7,))
    message = FakeMessage("/docs_health openrouter")

    asyncio.run(handlers.docs_health_command(_update(message, user_id=7), _context(services)))

    assert message.replies == ["fake docs health preview"]
    assert calls[0]["filter_text"] == "openrouter"
    assert calls[0]["is_allowed"] is True
    assert calls[0]["report_provider"] is provider


def test_help_mentions_docs_health() -> None:
    message = FakeMessage()

    asyncio.run(help_command(_update(message, user_id=7), _context(BotServices())))

    assert "/docs_health [service_id]" in message.replies[-1]


def _report() -> DocsHealthReport:
    return DocsHealthReport(
        sources=(
            _source("n8n", "n8n", "n8n_docs", current_status="healthy", status_reason="source is healthy"),
            _source("supabase", "Supabase", "supabase_docs", current_status="healthy", status_reason="source is healthy"),
            _source(
                "flutterflow",
                "FlutterFlow",
                "flutterflow_docs",
                current_status="inactive",
                active=False,
                stale_status="not_applicable",
                status_reason="docs status is configured_not_indexed",
                suggested_next_action="owner/admin may run read-only docs preview before any explicit activation",
            ),
            _source(
                "openrouter",
                "OpenRouter",
                "openrouter_docs",
                current_status="warning",
                status_reason="quality gate returned WARN; generator boilerplate found",
                suggested_next_action="inspect source configuration and run read-only docs preview if needed",
            ),
            _source(
                "telegram_bot_api",
                "Telegram Bot API",
                "telegram_bot_api_docs",
                current_status="failed",
                quality_status="FAIL",
                status_reason="quality gate returned FAIL; raw HTML markers; navigation/footer/cookie markers",
                suggested_next_action="review last quality errors before any explicit refresh",
            ),
        ),
        runtime_status="available",
    )


def _source(
    service_id: str,
    display_name: str,
    source_id: str,
    *,
    current_status: str,
    active: bool = True,
    stale_status: str = "fresh",
    status_reason: str,
    quality_status: str = "PASS",
    suggested_next_action: str = "no owner action required",
) -> DocsSourceHealth:
    return DocsSourceHealth(
        service_id=service_id,
        service_display_name=display_name,
        source_id=source_id,
        source_title=display_name,
        source_type="external_docs",
        registered=True,
        active=active,
        current_status=current_status,  # type: ignore[arg-type]
        status_reason=status_reason,
        docs_status="indexed" if active else "configured_not_indexed",
        quality_status=quality_status,
        status_notes=tuple(status_reason.split("; ")),
        last_checked_at=NOW,
        last_success_at=NOW,
        age_days=0,
        stale_status=stale_status,  # type: ignore[arg-type]
        stale_reason="last successful update is 0 days old; threshold is 30 days",
        document_count=1 if active else 0,
        chunk_count=10 if active else 0,
        owner_review_required=current_status != "healthy",
        suggested_next_action=suggested_next_action,
        automatic_refresh_allowed=False,
    )


def _context(services: BotServices) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}), args=None)


def _update(message: FakeMessage, *, user_id: int) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id), callback_query=None)
