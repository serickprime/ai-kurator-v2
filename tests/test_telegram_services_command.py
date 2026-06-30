import asyncio
from types import SimpleNamespace

from app.bot.handlers import BotServices, services_command
from app.service_registry.types import ServiceDocsStatus


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        del kwargs
        self.replies.append(text)


class FakeStatusProvider:
    def __init__(self, statuses: tuple[ServiceDocsStatus, ...] = (), error: Exception | None = None) -> None:
        self.statuses = statuses
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def list_statuses(self, **kwargs: object) -> tuple[ServiceDocsStatus, ...]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.statuses


def test_services_command_shows_indexed_and_not_configured_services() -> None:
    provider = FakeStatusProvider(
        (
            ServiceDocsStatus(
                service_id="n8n",
                display_name="n8n",
                aliases=("n8n",),
                docs_source="n8n_docs",
                configured_status="enabled",
                docs_status="indexed",
                active_docs_count=50,
                active_chunks_count=284,
                detected_documents_count=3,
                detected_chunks_count=9,
                quality_status="PASS",
                mention_count=12,
                docs_source_configured=True,
            ),
            ServiceDocsStatus(
                service_id="flutterflow",
                display_name="FlutterFlow",
                aliases=("flutterflow",),
                docs_source=None,
                configured_status="not_configured",
                docs_status="not_configured",
                detected_documents_count=1,
                detected_chunks_count=2,
                quality_status="none",
            ),
        )
    )
    services = BotServices(service_docs_status_provider=provider)
    message = FakeMessage()

    asyncio.run(services_command(_update(message), _context(services)))

    reply = message.replies[-1]
    assert "n8n — найден в базе, документация подключена, PASS" in reply
    assert "FlutterFlow — найден в базе, документация не подключена" in reply
    assert provider.calls == [{"scan_corpus": True}]


def test_services_command_does_not_show_json_or_dict_repr() -> None:
    provider = FakeStatusProvider(
        (
            ServiceDocsStatus(
                service_id="supabase",
                display_name="Supabase",
                aliases=("supabase",),
                docs_source="supabase_docs",
                configured_status="enabled",
                docs_status="indexed",
                active_docs_count=25,
                quality_status="PASS",
            ),
        )
    )
    services = BotServices(service_docs_status_provider=provider)
    message = FakeMessage()

    asyncio.run(services_command(_update(message), _context(services)))

    reply = message.replies[-1]
    assert "{" not in reply
    assert "ServiceDocsStatus" not in reply
    assert "docs_status" not in reply


def test_services_command_redacts_secret_errors() -> None:
    provider = FakeStatusProvider(error=RuntimeError("Bearer abc.def.ghi failed with sb_secret_hidden"))
    services = BotServices(service_docs_status_provider=provider)
    message = FakeMessage()

    asyncio.run(services_command(_update(message), _context(services)))

    reply = message.replies[-1]
    assert "Bearer <redacted>" in reply
    assert "abc.def.ghi" not in reply
    assert "sb_secret_hidden" not in reply


def _context(services: BotServices) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}))


def _update(message: FakeMessage) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=7), callback_query=None)
