import asyncio
from types import SimpleNamespace

from app.bot.features.docs_registry import format_docs_candidates, send_docs_dashboard
from app.bot.handlers import BotServices, docs_command
from app.docs_registry.models import DocsSourceCandidate
from app.service_registry.types import ServiceDocsStatus


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []
        self.reply_markups: list[object] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        self.replies.append(text)
        self.reply_markups.append(kwargs.get("reply_markup"))


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

    async def sync(self) -> None:
        self.mutation_calls.append("sync")

    async def index(self) -> None:
        self.mutation_calls.append("index")

    async def write(self) -> None:
        self.mutation_calls.append("write")


def test_docs_dashboard_shows_candidates() -> None:
    text = format_docs_candidates((), candidates=(_candidate("claude_code", "Claude Code", "claude_code_docs"),))

    assert "Можно подключить позже:" in text
    assert "➕ Claude Code" in text


def test_docs_dashboard_hides_already_connected_candidates() -> None:
    text = format_docs_candidates(
        (_status("claude_code", "Claude Code", "claude_code_docs"),),
        candidates=(
            _candidate("claude_code", "Claude Code", "claude_code_docs"),
            _candidate("openrouter", "OpenRouter", "openrouter_docs"),
        ),
    )

    assert "➕ Claude Code" not in text
    assert "➕ OpenRouter" in text


def test_docs_dashboard_truncates_long_candidate_list() -> None:
    candidates = tuple(_candidate(f"service_{index}", f"Service {index}", f"service_{index}_docs") for index in range(12))

    text = format_docs_candidates((), candidates=candidates)

    assert "➕ Service 9" in text
    assert "➕ Service 10" not in text
    assert "Ещё: 2" in text


def test_docs_command_loads_real_candidates_without_mutations() -> None:
    provider = FakeDocsStatusProvider((_status("n8n", "n8n", "n8n_docs"),))
    services = BotServices(service_docs_status_provider=provider, owner_ids=(7,))
    message = FakeMessage()

    asyncio.run(docs_command(_update(message), _context(services)))

    assert provider.calls == [{"scan_corpus": False}]
    assert provider.mutation_calls == []
    assert "Документация сервисов" in message.replies[-1]
    assert "➕ Можно подключить:" in message.replies[-1]
    assert message.reply_markups[-1] is not None


def test_docs_dashboard_works_when_candidates_config_is_unavailable() -> None:
    provider = FakeDocsStatusProvider((_status("n8n", "n8n", "n8n_docs"),))
    message = FakeMessage()

    asyncio.run(
        send_docs_dashboard(
            _update(message),
            status_provider=provider,
            is_allowed=True,
            candidate_loader=_broken_candidate_loader,
        )
    )

    assert provider.calls == [{"scan_corpus": False}]
    assert "✅ Подключено: 1" in message.replies[-1]
    assert "➕ Можно подключить: 0" in message.replies[-1]


def test_docs_dashboard_with_candidates_does_not_show_raw_json() -> None:
    text = format_docs_candidates((), candidates=(_candidate("openrouter", "OpenRouter", "openrouter_docs"),))

    assert "{" not in text
    assert "DocsSourceCandidate" not in text
    assert "official_start_urls" not in text


def _broken_candidate_loader() -> tuple[DocsSourceCandidate, ...]:
    raise RuntimeError("catalog unavailable")


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


def _status(service_id: str, display_name: str, docs_source: str) -> ServiceDocsStatus:
    return ServiceDocsStatus(
        service_id=service_id,
        display_name=display_name,
        aliases=(service_id,),
        docs_source=docs_source,
        configured_status="enabled",
        docs_status="indexed",
        active_docs_count=10,
        active_chunks_count=50,
        quality_status="PASS",
        docs_source_configured=True,
    )


def _context(services: BotServices) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}))


def _update(message: FakeMessage) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=7), callback_query=None)
