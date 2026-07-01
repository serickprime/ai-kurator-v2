import asyncio
from datetime import datetime, timezone
from typing import Any

from app.db.repositories import DocumentRecord, SectionRecord
from app.external_docs.indexer import ExternalDocsIndexer
from app.external_docs.types import ExternalDocSource, ExtractedPage


class FakeEmbeddingClient:
    async def embed(self, text: str) -> list[float]:
        return (await self.embed_many([text]))[0]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [[0.01 * (index % 13) for index in range(1024)] for _ in texts]


class FakeRepository:
    def __init__(self) -> None:
        self.workspace = {"id": "workspace-1", "name": "team"}
        self.documents: list[dict[str, Any]] = []
        self.cards: list[dict[str, Any]] = []
        self.sections: list[dict[str, Any]] = []
        self.chunks: list[dict[str, Any]] = []
        self.term_refreshes = 0

    async def get_or_create_workspace(self, name: str) -> dict[str, Any]:
        self.workspace = {"id": "workspace-1", "name": name}
        return self.workspace

    async def get_latest_document(self, workspace_id: str, document_key: str) -> DocumentRecord | None:
        rows = [row for row in self.documents if row["workspace_id"] == workspace_id and row["document_key"] == document_key]
        return _record(max(rows, key=lambda row: row["version"])) if rows else None

    async def get_active_document(self, workspace_id: str, document_key: str) -> DocumentRecord | None:
        rows = [
            row
            for row in self.documents
            if row["workspace_id"] == workspace_id and row["document_key"] == document_key and row["status"] == "active"
        ]
        return _record(max(rows, key=lambda row: row["version"])) if rows else None

    async def create_document(self, **kwargs: object) -> DocumentRecord:
        row = dict(kwargs)
        row["id"] = f"doc-{len(self.documents) + 1}"
        self.documents.append(row)
        return _record(row)

    async def archive_active_documents(self, workspace_id: str, document_key: str) -> None:
        for row in self.documents:
            if row["workspace_id"] == workspace_id and row["document_key"] == document_key and row["status"] == "active":
                row["status"] = "archived"

    async def activate_document(self, document_id: str) -> None:
        for row in self.documents:
            if row["id"] == document_id:
                row["status"] = "active"

    async def create_document_card(self, **kwargs: object) -> dict[str, object]:
        row = dict(kwargs)
        row["id"] = f"card-{len(self.cards) + 1}"
        self.cards.append(row)
        return row

    async def create_sections(self, **kwargs: object) -> list[SectionRecord]:
        records: list[SectionRecord] = []
        for section in kwargs["sections"]:
            row = {
                "id": f"section-{len(self.sections) + 1}",
                "document_id": kwargs["document_id"],
                "workspace_id": kwargs["workspace_id"],
                "section_index": section.section_index,
                "heading": section.heading,
                "metadata": section.metadata,
            }
            self.sections.append(row)
            records.append(SectionRecord(id=row["id"], section_index=section.section_index))
        return records

    async def create_chunks(self, **kwargs: object) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for chunk in kwargs["chunks"]:
            row = {
                "id": f"chunk-{len(self.chunks) + 1}",
                "document_id": kwargs["document_id"],
                "workspace_id": kwargs["workspace_id"],
                "section_id": kwargs["section_ids_by_index"][chunk.section_index],
                "content": chunk.content,
                "heading": chunk.heading,
                "metadata": chunk.metadata,
            }
            self.chunks.append(row)
            rows.append(row)
        return rows

    async def refresh_term_statistics(self, workspace_id: str) -> int:
        self.term_refreshes += 1
        return len(self.chunks)

    def active_documents(self) -> list[dict[str, Any]]:
        return [row for row in self.documents if row["status"] == "active"]


def test_external_docs_indexer_skips_unchanged_page_without_duplicates() -> None:
    repository = FakeRepository()
    indexer = ExternalDocsIndexer(repository=repository, embedding_client=FakeEmbeddingClient())
    page = _page(content_hash="hash-1", body="Use the HTTP Request node to call an API.")

    first = asyncio.run(indexer.index_page(page, _source()))
    second = asyncio.run(indexer.index_page(page, _source()))

    assert not first.skipped
    assert second.skipped
    assert second.document_id == first.document_id
    assert len(repository.documents) == 1
    assert len(repository.active_documents()) == 1
    assert len(repository.cards) == 1
    assert repository.documents[0]["source_type"] == "external_docs"
    assert repository.documents[0]["metadata"]["source_kind"] == "external_docs"
    assert repository.cards[0]["card"].metadata["content_type"] == ["official_docs", "external_docs"]
    assert all(row["metadata"]["canonical_url"] == page.canonical_url for row in repository.chunks)


def test_external_docs_indexer_archives_old_version_when_page_changes() -> None:
    repository = FakeRepository()
    indexer = ExternalDocsIndexer(repository=repository, embedding_client=FakeEmbeddingClient())

    first = asyncio.run(indexer.index_page(_page(content_hash="hash-1", body="Old docs text."), _source()))
    second = asyncio.run(indexer.index_page(_page(content_hash="hash-2", body="New docs text."), _source()))

    assert not first.skipped
    assert not second.skipped
    assert second.version == 2
    assert repository.documents[0]["status"] == "archived"
    assert [row["id"] for row in repository.active_documents()] == [second.document_id]
    assert second.archived_old


def test_external_docs_indexer_filters_low_value_chunks_without_dropping_technical_chunks() -> None:
    repository = FakeRepository()
    indexer = ExternalDocsIndexer(repository=repository, embedding_client=FakeEmbeddingClient())
    page = _page(
        content_hash="hash-1",
        body=(
            "This page explains setup with enough descriptive text for useful grounded answers."
            "\n\n## AI Tools"
            "\n\n## Terminal\n\nnpm run dev"
            "\n\n## API\n\nGET /rest/v1/instruments"
            "\n\n## Config\n\nPUBLIC_SUPABASE_URL=https://docs.example.com"
        ),
    )

    result = asyncio.run(indexer.index_page(page, _source()))
    contents = [str(row["content"]) for row in repository.chunks]

    assert result.chunks_count == len(repository.chunks)
    assert not any(content.strip() == "## AI Tools" for content in contents)
    assert any("npm run dev" in content for content in contents)
    assert any("GET /rest/v1/instruments" in content for content in contents)
    assert any("PUBLIC_SUPABASE_URL=https://docs.example.com" in content for content in contents)


def _source() -> ExternalDocSource:
    return ExternalDocSource(
        name="docs",
        source_kind="external_docs",
        allowed_domains=("docs.example.com",),
        start_urls=("https://docs.example.com/"),
    )


def _page(*, content_hash: str, body: str) -> ExtractedPage:
    return ExtractedPage(
        source_name="docs",
        source_url="https://docs.example.com/integrations/http-request",
        canonical_url="https://docs.example.com/integrations/http-request",
        title="HTTP Request node",
        structured_text=f"# HTTP Request node\n\n{body}",
        content_hash=content_hash,
        headings=("HTTP Request node",),
        crawled_at=datetime.now(timezone.utc),
    )


def _record(row: dict[str, Any]) -> DocumentRecord:
    return DocumentRecord(
        id=str(row["id"]),
        version=int(row["version"]),
        status=str(row["status"]),
        content_hash=str(row["content_hash"]),
        metadata=row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
    )
