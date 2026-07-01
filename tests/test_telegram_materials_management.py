import asyncio
from types import SimpleNamespace
from typing import Any

from app.bot.handlers import BotServices, archive_material_command, handle_text, help_command, material_command, materials_command
from app.bot.materials import (
    ExternalDocsArchiveError,
    MaterialCard,
    MaterialNotFoundError,
    MaterialsProvider,
    format_material_card,
    format_materials_list,
)


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
        return SimpleNamespace(answer="RAG answer", status="answered", sources=(), debug={})


class FakeMaterialsProvider:
    def __init__(
        self,
        materials: tuple[MaterialCard, ...] = (),
        *,
        card: MaterialCard | None = None,
        archive_card: MaterialCard | None = None,
        error: Exception | None = None,
    ) -> None:
        self.materials = materials
        self.card = card or (materials[0] if materials else _card("aaaaaaaa-0000-0000-0000-000000000000", "lesson.txt"))
        self.archive_card = archive_card or self.card
        self.error = error
        self.list_calls: list[tuple[str, int]] = []
        self.get_calls: list[tuple[str, str]] = []
        self.archive_calls: list[tuple[str, str]] = []

    async def list_recent_materials(self, workspace_id: str, limit: int = 10) -> tuple[MaterialCard, ...]:
        self.list_calls.append((workspace_id, limit))
        if self.error is not None:
            raise self.error
        return self.materials

    async def get_material(self, workspace_id: str, material_id_or_prefix: str) -> MaterialCard:
        self.get_calls.append((workspace_id, material_id_or_prefix))
        if self.error is not None:
            raise self.error
        return self.card

    async def archive_material(self, workspace_id: str, material_id_or_prefix: str) -> MaterialCard:
        self.archive_calls.append((workspace_id, material_id_or_prefix))
        if self.error is not None:
            raise self.error
        return self.archive_card


class FakeSupabaseClient:
    def __init__(self, documents: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> None:
        self.documents = documents
        self.chunks = chunks
        self.select_calls: list[tuple[str, dict[str, Any] | None]] = []
        self.update_calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.delete_calls: list[tuple[str, dict[str, Any]]] = []

    async def select(self, table: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.select_calls.append((table, params))
        if table == "documents":
            rows = list(self.documents)
            status_filter = str((params or {}).get("status") or "")
            if status_filter.startswith("eq."):
                expected = status_filter.removeprefix("eq.")
                rows = [row for row in rows if row.get("status") == expected]
            return rows
        if table == "chunks":
            document_filter = str((params or {}).get("document_id") or "")
            if document_filter.startswith("in.(") and document_filter.endswith(")"):
                document_ids = set(document_filter.removeprefix("in.(").removesuffix(")").split(","))
                return [row for row in self.chunks if str(row.get("document_id") or "") in document_ids]
            return list(self.chunks)
        return []

    async def update(
        self,
        table: str,
        payload: dict[str, Any],
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        self.update_calls.append((table, payload, params))
        if table != "documents":
            return []
        document_id = str(params.get("id") or "").removeprefix("eq.")
        updated: list[dict[str, Any]] = []
        for row in self.documents:
            if row.get("id") == document_id:
                row.update(payload)
                updated.append(dict(row))
        return updated


def test_materials_provider_lists_uploaded_docs_only() -> None:
    local_id = "11111111-1111-1111-1111-111111111111"
    external_id = "22222222-2222-2222-2222-222222222222"
    provider = MaterialsProvider(
        FakeSupabaseClient(
            documents=[
                _document(local_id, "lesson.txt", source_type="text", services=("n8n",)),
                _document(external_id, "External docs", source_type="external_docs"),
            ],
            chunks=[
                {"id": "chunk-1", "document_id": local_id},
                {"id": "chunk-2", "document_id": local_id},
                {"id": "external-chunk", "document_id": external_id},
            ],
        )
    )

    materials = asyncio.run(provider.list_recent_materials("workspace-1"))

    assert len(materials) == 1
    assert materials[0].document_id == local_id
    assert materials[0].chunks_count == 2
    assert materials[0].service_labels == ("n8n",)


def test_materials_provider_archives_without_deleting_chunks() -> None:
    document_id = "33333333-3333-3333-3333-333333333333"
    client = FakeSupabaseClient(
        documents=[_document(document_id, "lesson.txt", source_type="text")],
        chunks=[{"id": "chunk-1", "document_id": document_id}],
    )
    provider = MaterialsProvider(client)

    archived = asyncio.run(provider.archive_material("workspace-1", "33333333"))

    assert archived.status == "archived"
    assert archived.chunks_count == 1
    assert client.update_calls == [
        (
            "documents",
            {"status": "archived"},
            {"id": f"eq.{document_id}", "workspace_id": "eq.workspace-1", "status": "eq.active"},
        )
    ]
    assert client.delete_calls == []


def test_materials_provider_rejects_external_docs_archive() -> None:
    provider = MaterialsProvider(
        FakeSupabaseClient(
            documents=[
                _document(
                    "44444444-4444-4444-4444-444444444444",
                    "Official page",
                    source_type="external_docs",
                )
            ],
            chunks=[],
        )
    )

    try:
        asyncio.run(provider.archive_material("workspace-1", "44444444"))
    except ExternalDocsArchiveError:
        pass
    else:
        raise AssertionError("External docs archive should be rejected")


def test_materials_command_shows_recent_materials_and_not_json() -> None:
    material = _card("aaaaaaaa-0000-0000-0000-000000000000", "lesson.txt", services=("n8n",))
    provider = FakeMaterialsProvider((material,))
    services = _services(provider)
    message = FakeMessage("/materials")

    asyncio.run(materials_command(_update(message), _context(services)))

    reply = message.replies[-1]
    assert provider.list_calls == [("workspace-1", 10)]
    assert "[aaaaaaaa]" in reply
    assert "lesson.txt" in reply
    assert "n8n" in reply
    assert "{" not in reply
    assert "MaterialCard" not in reply


def test_material_command_uses_short_id() -> None:
    material = _card("bbbbbbbb-0000-0000-0000-000000000000", "lesson.txt")
    provider = FakeMaterialsProvider(card=material)
    message = FakeMessage("/material bbbbbbbb")

    asyncio.run(material_command(_update(message), _context(_services(provider))))

    assert provider.get_calls == [("workspace-1", "bbbbbbbb")]
    assert "bbbbbbbb" in message.replies[-1]
    assert "Chunks:" in message.replies[-1]


def test_material_command_handles_unknown_id() -> None:
    provider = FakeMaterialsProvider(error=MaterialNotFoundError("missing"))
    message = FakeMessage("/material missing")

    asyncio.run(material_command(_update(message), _context(_services(provider))))

    assert "не найден" in message.replies[-1].casefold()


def test_archive_material_command_archives_uploaded_doc() -> None:
    material = _card("cccccccc-0000-0000-0000-000000000000", "lesson.txt", status="archived")
    provider = FakeMaterialsProvider(archive_card=material)
    message = FakeMessage("/archive_material cccccccc")

    asyncio.run(archive_material_command(_update(message), _context(_services(provider))))

    assert provider.archive_calls == [("workspace-1", "cccccccc")]
    assert "lesson.txt" in message.replies[-1]


def test_archive_material_command_rejects_external_docs() -> None:
    provider = FakeMaterialsProvider(error=ExternalDocsArchiveError("external"))
    message = FakeMessage("/archive_material external")

    asyncio.run(archive_material_command(_update(message), _context(_services(provider))))

    assert "документац" in message.replies[-1].casefold()


def test_archive_material_command_requires_owner_or_admin() -> None:
    provider = FakeMaterialsProvider()
    services = BotServices(
        owner_ids=(7,),
        default_workspace_id="workspace-1",
        materials_provider=provider,
    )
    message = FakeMessage("/archive_material aaaaaaaa")

    asyncio.run(archive_material_command(_update(message, user_id=8), _context(services)))

    assert provider.archive_calls == []
    assert "владельц" in message.replies[-1].casefold()


def test_materials_text_fallback_does_not_call_rag() -> None:
    pipeline = FakeRagPipeline()
    provider = FakeMaterialsProvider((_card("dddddddd-0000-0000-0000-000000000000", "lesson.txt"),))
    services = _services(provider)
    services.rag_pipeline = pipeline
    message = FakeMessage("/materials")

    asyncio.run(handle_text(_update(message), _context(services)))

    assert provider.list_calls == [("workspace-1", 10)]
    assert pipeline.calls == []


def test_help_mentions_material_management_commands() -> None:
    message = FakeMessage()

    asyncio.run(help_command(_update(message), _context(BotServices())))

    reply = message.replies[-1]
    assert "/materials" in reply
    assert "/material <id>" in reply
    assert "/archive_material <id>" in reply


def test_material_formatters_show_local_card_without_raw_dict() -> None:
    material = _card(
        "eeeeeeee-0000-0000-0000-000000000000",
        "lesson.txt",
        services=("Supabase",),
    )

    text = format_materials_list((material,)) + "\n" + format_material_card(material)

    assert "lesson.txt" in text
    assert "Supabase" in text
    assert "{" not in text
    assert "MaterialCard" not in text


def _services(provider: FakeMaterialsProvider) -> BotServices:
    return BotServices(
        owner_ids=(7,),
        default_workspace_id="workspace-1",
        materials_provider=provider,
    )


def _context(services: BotServices) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}), args=None)


def _update(message: FakeMessage, *, user_id: int = 7) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id), callback_query=None)


def _document(
    document_id: str,
    title: str,
    *,
    source_type: str,
    status: str = "active",
    services: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "id": document_id,
        "filename": title,
        "document_key": title,
        "title": title,
        "status": status,
        "source_type": source_type,
        "metadata": {"service_ids": list(services)},
    }


def _card(
    document_id: str,
    title: str,
    *,
    status: str = "active",
    services: tuple[str, ...] = (),
) -> MaterialCard:
    return MaterialCard(
        document_id=document_id,
        title=title,
        source_type="text",
        status=status,
        chunks_count=3,
        service_ids=services,
    )
