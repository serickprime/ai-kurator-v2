import asyncio
from types import SimpleNamespace
from typing import Any

from app.bot.base_status import BaseStatus, BaseStatusProvider, ExternalSourceStatus, RecentDocument, format_base_status
from app.bot.handlers import BotServices, base_status_command
from app.service_registry.types import ServiceDocsStatus


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        del kwargs
        self.replies.append(text)


class FakeBaseStatusProvider:
    def __init__(self, status: BaseStatus | None = None, error: Exception | None = None) -> None:
        self.status = status or BaseStatus()
        self.error = error
        self.calls = 0

    async def get_status(self) -> BaseStatus:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.status


class FakeServiceStatusProvider:
    def __init__(self, statuses: tuple[ServiceDocsStatus, ...]) -> None:
        self.statuses = statuses
        self.calls: list[dict[str, object]] = []

    async def list_statuses(self, **kwargs: object) -> tuple[ServiceDocsStatus, ...]:
        self.calls.append(kwargs)
        return self.statuses


class FakeSupabaseClient:
    def __init__(self, documents: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> None:
        self.documents = documents
        self.chunks = chunks
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def select(self, table: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.calls.append((table, params))
        if table == "documents":
            return self.documents
        if table == "chunks":
            document_filter = str((params or {}).get("document_id") or "")
            if document_filter.startswith("in.(") and document_filter.endswith(")"):
                document_ids = set(document_filter.removeprefix("in.(").removesuffix(")").split(","))
                return [row for row in self.chunks if str(row.get("document_id") or "") in document_ids]
            return self.chunks
        return []


def test_base_status_provider_counts_documents_and_active_chunks() -> None:
    service_statuses = (
        _service_status("n8n", "n8n", "n8n_docs", active_docs=50, active_chunks=284, quality="PASS"),
    )
    provider = BaseStatusProvider(
        FakeSupabaseClient(
            documents=[
                _document("local-1", status="active", source_type="text", title="Local material"),
                _document("external-1", status="active", source_type="external_docs", title="External page"),
                _document("old-1", status="archived", source_type="text", title="Old material"),
            ],
            chunks=[
                {"id": "chunk-1", "document_id": "local-1"},
                {"id": "chunk-2", "document_id": "external-1"},
                {"id": "old-chunk", "document_id": "old-1"},
            ],
        ),
        service_status_provider=FakeServiceStatusProvider(service_statuses),
    )

    status = asyncio.run(provider.get_status())

    assert status.active_documents_count == 2
    assert status.active_chunks_count == 2
    assert status.uploaded_documents_count == 1
    assert status.external_documents_count == 1
    assert status.archived_documents_count == 1
    assert status.external_sources[0].source_name == "n8n_docs"
    assert status.external_sources[0].quality_status == "PASS"


def test_base_status_format_contains_counts_external_services_and_recent_docs() -> None:
    text = format_base_status(
        BaseStatus(
            active_documents_count=85,
            active_chunks_count=1250,
            uploaded_documents_count=10,
            external_documents_count=75,
            archived_documents_count=20,
            external_sources=(
                ExternalSourceStatus("n8n_docs", active_docs_count=50, quality_status="PASS"),
                ExternalSourceStatus("supabase_docs", active_docs_count=25, quality_status="PASS"),
            ),
            services=(
                _service_status("n8n", "n8n", "n8n_docs"),
                _service_status("supabase", "Supabase", "supabase_docs"),
                _service_status("flutterflow", "FlutterFlow", None, docs_status="not_configured"),
            ),
            recent_documents=(RecentDocument("CLn02_text_double_deep"), RecentDocument("service_discovery_test")),
        )
    )

    assert "Документы: 85 active" in text
    assert "Chunks: 1250" in text
    assert "n8n_docs — 50 docs, PASS" in text
    assert "supabase_docs — 25 docs, PASS" in text
    assert "n8n — документация подключена" in text
    assert "FlutterFlow — документация не подключена" in text
    assert "- CLn02_text_double_deep" in text
    assert "- service_discovery_test" in text


def test_base_status_format_does_not_show_json_or_dict_repr() -> None:
    text = format_base_status(BaseStatus(active_documents_count=1, recent_documents=(RecentDocument("lesson"),)))

    assert "{" not in text
    assert "BaseStatus" not in text
    assert "ServiceDocsStatus" not in text
    assert "docs_status" not in text


def test_base_status_format_handles_empty_base() -> None:
    text = format_base_status(BaseStatus())

    assert "Документы: 0 active" in text
    assert "Chunks: 0" in text
    assert "нет данных" in text


def test_base_status_format_truncates_long_lists() -> None:
    text = format_base_status(
        BaseStatus(
            external_sources=tuple(
                ExternalSourceStatus(f"source_{index}", active_docs_count=index, quality_status="PASS")
                for index in range(12)
            ),
            services=tuple(_service_status(f"service_{index}", f"Service {index}", f"source_{index}") for index in range(12)),
            recent_documents=tuple(RecentDocument(f"doc-{index}") for index in range(7)),
        )
    )

    assert "source_9" in text
    assert "source_10" not in text
    assert "Service 9" in text
    assert "Service 10" not in text
    assert "- doc-4" in text
    assert "- doc-5" not in text


def test_base_status_command_replies_with_formatted_status() -> None:
    provider = FakeBaseStatusProvider(BaseStatus(active_documents_count=2, active_chunks_count=7))
    services = BotServices(base_status_provider=provider)
    message = FakeMessage()

    asyncio.run(base_status_command(_update(message), _context(services)))

    assert provider.calls == 1
    assert "Документы: 2 active" in message.replies[-1]
    assert "Chunks: 7" in message.replies[-1]


def test_base_status_command_redacts_secret_errors() -> None:
    provider = FakeBaseStatusProvider(error=RuntimeError("Bearer abc.def.ghi failed with sb_secret_hidden"))
    services = BotServices(base_status_provider=provider)
    message = FakeMessage()

    asyncio.run(base_status_command(_update(message), _context(services)))

    reply = message.replies[-1]
    assert "Bearer <redacted>" in reply
    assert "abc.def.ghi" not in reply
    assert "sb_secret_hidden" not in reply


def _service_status(
    service_id: str,
    display_name: str,
    docs_source: str | None,
    *,
    active_docs: int = 1,
    active_chunks: int = 1,
    quality: str = "PASS",
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
        active_chunks_count=active_chunks,
        quality_status=quality,
        docs_source_configured=bool(docs_source),
    )


def _document(document_id: str, *, status: str, source_type: str, title: str) -> dict[str, object]:
    return {
        "id": document_id,
        "filename": f"{document_id}.txt",
        "document_key": f"{document_id}.txt",
        "title": title,
        "status": status,
        "source_type": source_type,
        "metadata": {},
    }


def _context(services: BotServices) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}))


def _update(message: FakeMessage) -> SimpleNamespace:
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=7), callback_query=None)
