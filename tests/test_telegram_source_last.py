import asyncio
from types import SimpleNamespace
from typing import Any

from app.bot.handlers import BotServices, _answer_intake, archive_source_command, handle_text, help_command, source_last_command
from app.bot.intake_buffer import UserIntake
from app.bot.materials import ExternalDocsArchiveError, MaterialCard, MaterialsProvider
from app.rag.types import SourceRef


LOCAL_DOCUMENT_ID = "2c68cef6-1111-2222-3333-444444444444"
EXTERNAL_DOCUMENT_ID = "9c68cef6-1111-2222-3333-444444444444"


class FakeMessage:
    def __init__(self, text: str = "") -> None:
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
        return SimpleNamespace(
            answer="answer",
            status="answered",
            sources=(
                SourceRef(
                    document_id=LOCAL_DOCUMENT_ID,
                    document_title="ClaudeCode",
                    metadata={"source_type": "markdown", "filename": "ClaudeCode.md"},
                ),
            ),
            debug={
                "accepted_evidence": [
                    {"document_id": LOCAL_DOCUMENT_ID},
                    {"document_id": LOCAL_DOCUMENT_ID},
                ]
            },
        )


class FakeMaterialsProvider:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.archive_calls: list[tuple[str, str]] = []

    async def list_recent_materials(self, workspace_id: str, limit: int = 10) -> tuple[MaterialCard, ...]:
        del workspace_id, limit
        return ()

    async def get_material(self, workspace_id: str, material_id_or_prefix: str) -> MaterialCard:
        del workspace_id, material_id_or_prefix
        raise AssertionError("get_material should not be called")

    async def archive_material(self, workspace_id: str, material_id_or_prefix: str) -> MaterialCard:
        self.archive_calls.append((workspace_id, material_id_or_prefix))
        if self.error is not None:
            raise self.error
        return MaterialCard(
            document_id=material_id_or_prefix,
            title="ClaudeCode",
            source_type="markdown",
            status="archived",
        )


class FakeSupabaseClient:
    def __init__(self) -> None:
        self.documents = [
            {
                "id": LOCAL_DOCUMENT_ID,
                "filename": "ClaudeCode.md",
                "document_key": "ClaudeCode.md",
                "title": "ClaudeCode",
                "status": "active",
                "source_type": "markdown",
                "metadata": {},
            }
        ]
        self.chunks = [{"id": "chunk-1", "document_id": LOCAL_DOCUMENT_ID}]
        self.update_calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.delete_calls: list[tuple[str, dict[str, Any]]] = []

    async def select(self, table: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if table == "documents":
            return list(self.documents)
        if table == "chunks":
            return list(self.chunks)
        return []

    async def update(
        self,
        table: str,
        payload: dict[str, Any],
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        self.update_calls.append((table, payload, params))
        row = dict(self.documents[0])
        row.update(payload)
        return [row]


def test_source_last_without_last_answer_shows_clear_message() -> None:
    message = FakeMessage("/source_last")

    asyncio.run(source_last_command(_update(message), _context(_services())))

    assert "Пока нет данных" in message.replies[-1]


def test_source_last_shows_uploaded_local_source_from_last_answer() -> None:
    services = _services()
    services.state_store.get(7).last_debug = _last_debug()
    message = FakeMessage("/source_last")

    asyncio.run(source_last_command(_update(message), _context(services)))

    reply = message.replies[-1]
    assert "[2c68cef6]" in reply
    assert "ClaudeCode" in reply
    assert "тип: markdown" in reply
    assert "chunks: 2" in reply
    assert "источник: uploaded" in reply
    assert LOCAL_DOCUMENT_ID not in reply


def test_source_last_shows_external_docs_as_official_without_archive_command() -> None:
    services = _services()
    services.state_store.get(7).last_debug = _last_debug(sources=("external",))
    message = FakeMessage("/source_last")

    asyncio.run(source_last_command(_update(message), _context(services)))

    reply = message.replies[-1]
    assert "[n8n_docs]" in reply
    assert "n8n official docs" in reply
    assert "тип: external_docs" in reply
    assert "источник: official" in reply
    assert "/archive_source" not in reply


def test_source_last_does_not_show_raw_json() -> None:
    services = _services()
    services.state_store.get(7).last_debug = _last_debug(sources=("local", "external"))
    message = FakeMessage("/source_last")

    asyncio.run(source_last_command(_update(message), _context(services)))

    reply = message.replies[-1]
    assert "{" not in reply
    assert "source_refs" not in reply
    assert "metadata" not in reply


def test_archive_source_archives_uploaded_source_from_last_answer() -> None:
    provider = FakeMaterialsProvider()
    services = _services(materials_provider=provider)
    services.state_store.get(7).last_debug = _last_debug()
    message = FakeMessage("/archive_source 2c68cef6")

    asyncio.run(archive_source_command(_update(message), _context(services)))

    assert provider.archive_calls == [("workspace-1", LOCAL_DOCUMENT_ID)]
    assert "Источник архивирован: ClaudeCode" in message.replies[-1]


def test_archive_source_rejects_external_docs() -> None:
    provider = FakeMaterialsProvider(error=ExternalDocsArchiveError("external"))
    services = _services(materials_provider=provider)
    services.state_store.get(7).last_debug = _last_debug(sources=("external",))
    message = FakeMessage("/archive_source n8n_docs")

    asyncio.run(archive_source_command(_update(message), _context(services)))

    assert provider.archive_calls == []
    assert "Официальную документацию нельзя архивировать" in message.replies[-1]


def test_archive_source_requires_owner_or_admin() -> None:
    provider = FakeMaterialsProvider()
    services = BotServices(
        owner_ids=(7,),
        default_workspace_id="workspace-1",
        materials_provider=provider,
    )
    services.state_store.get(8).last_debug = _last_debug()
    message = FakeMessage("/archive_source 2c68cef6")

    asyncio.run(archive_source_command(_update(message, user_id=8), _context(services)))

    assert provider.archive_calls == []
    assert "Архивирование доступно владельцу бота" in message.replies[-1]


def test_archive_source_unknown_id_mentions_last_answer() -> None:
    provider = FakeMaterialsProvider()
    services = _services(materials_provider=provider)
    services.state_store.get(7).last_debug = _last_debug()
    message = FakeMessage("/archive_source missing")

    asyncio.run(archive_source_command(_update(message), _context(services)))

    assert provider.archive_calls == []
    assert "Такого источника нет в последнем ответе" in message.replies[-1]


def test_archive_source_uses_materials_provider_without_deleting_chunks() -> None:
    client = FakeSupabaseClient()
    services = _services(materials_provider=MaterialsProvider(client))
    services.state_store.get(7).last_debug = _last_debug()
    message = FakeMessage("/archive_source 2c68cef6")

    asyncio.run(archive_source_command(_update(message), _context(services)))

    assert client.update_calls == [
        (
            "documents",
            {"status": "archived"},
            {"id": f"eq.{LOCAL_DOCUMENT_ID}", "workspace_id": "eq.workspace-1", "status": "eq.active"},
        )
    ]
    assert client.delete_calls == []


def test_source_last_text_fallback_does_not_call_rag() -> None:
    pipeline = FakeRagPipeline()
    services = _services()
    services.rag_pipeline = pipeline
    services.state_store.get(7).last_debug = _last_debug()
    message = FakeMessage("/source_last")

    asyncio.run(handle_text(_update(message), _context(services)))

    assert pipeline.calls == []
    assert "[2c68cef6]" in message.replies[-1]


def test_archive_source_text_fallback_does_not_call_rag() -> None:
    pipeline = FakeRagPipeline()
    provider = FakeMaterialsProvider()
    services = _services(materials_provider=provider)
    services.rag_pipeline = pipeline
    services.state_store.get(7).last_debug = _last_debug()
    message = FakeMessage("/archive_source 2c68cef6")

    asyncio.run(handle_text(_update(message), _context(services)))

    assert pipeline.calls == []
    assert provider.archive_calls == [("workspace-1", LOCAL_DOCUMENT_ID)]


def test_help_mentions_source_last() -> None:
    message = FakeMessage("/help")

    asyncio.run(help_command(_update(message), _context(_services())))

    assert "/source_last" in message.replies[-1]
    assert "/archive_source <id>" in message.replies[-1]


def test_answer_intake_stores_structured_last_sources() -> None:
    services = _services()
    services.rag_pipeline = FakeRagPipeline()
    message = FakeMessage("question")

    asyncio.run(_answer_intake(_update(message), services, UserIntake(text="question")))

    debug = services.state_store.get(7).last_debug
    assert debug["source_refs"][0]["document_id"] == LOCAL_DOCUMENT_ID
    assert debug["source_refs"][0]["metadata"]["source_type"] == "markdown"


def _services(materials_provider: object | None = None) -> BotServices:
    return BotServices(
        owner_ids=(7,),
        default_workspace_id="workspace-1",
        materials_provider=materials_provider,
    )


def _context(services: BotServices) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}), args=None)


def _update(message: FakeMessage, *, user_id: int = 7) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id), callback_query=None)


def _last_debug(*, sources: tuple[str, ...] = ("local",)) -> dict[str, object]:
    source_refs: list[dict[str, object]] = []
    accepted_evidence: list[dict[str, object]] = []
    if "local" in sources:
        source_refs.append(
            {
                "document_id": LOCAL_DOCUMENT_ID,
                "document_title": "ClaudeCode",
                "metadata": {"source_type": "markdown", "filename": "ClaudeCode.md"},
            }
        )
        accepted_evidence.extend([{"document_id": LOCAL_DOCUMENT_ID}, {"document_id": LOCAL_DOCUMENT_ID}])
    if "external" in sources:
        source_refs.append(
            {
                "document_id": EXTERNAL_DOCUMENT_ID,
                "document_title": "n8n official docs",
                "source_uri": "https://docs.n8n.io/",
                "metadata": {
                    "source_kind": "external_docs",
                    "source_name": "n8n_docs",
                    "canonical_url": "https://docs.n8n.io/",
                },
            }
        )
        accepted_evidence.append({"document_id": EXTERNAL_DOCUMENT_ID})
    return {
        "status": "answered",
        "sources": ["ClaudeCode"],
        "source_refs": source_refs,
        "rag": {"accepted_evidence": accepted_evidence},
    }
