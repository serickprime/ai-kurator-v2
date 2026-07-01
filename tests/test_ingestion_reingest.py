import asyncio
from pathlib import Path
from typing import Any

from app.db.repositories import DocumentRecord, SectionRecord
from app.ingestion.indexing import IndexingService, file_content_hash


class FakeEmbeddingClient:
    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    async def embed(self, text: str) -> list[float]:
        return (await self.embed_many([text]))[0]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [[float(index % 11) / 11 for index in range(self.dim)] for _ in texts]


class FakeRepository:
    def __init__(self) -> None:
        self.workspace = {"id": "workspace-1", "name": "team"}
        self.documents: list[dict[str, Any]] = []
        self.cards: list[dict[str, Any]] = []
        self.sections: list[dict[str, Any]] = []
        self.chunks: list[dict[str, Any]] = []
        self.term_refreshes: list[str] = []

    async def get_or_create_workspace(self, name: str) -> dict[str, Any]:
        self.workspace = {"id": "workspace-1", "name": name}
        return self.workspace

    async def get_latest_document(self, workspace_id: str, document_key: str) -> DocumentRecord | None:
        rows = [
            row
            for row in self.documents
            if row["workspace_id"] == workspace_id and row["document_key"] == document_key
        ]
        if not rows:
            return None
        return _record(max(rows, key=lambda row: row["version"]))

    async def get_active_document(self, workspace_id: str, document_key: str) -> DocumentRecord | None:
        rows = [
            row
            for row in self.documents
            if row["workspace_id"] == workspace_id
            and row["document_key"] == document_key
            and row["status"] == "active"
        ]
        if not rows:
            return None
        return _record(max(rows, key=lambda row: row["version"]))

    async def create_document(self, **kwargs: object) -> DocumentRecord:
        row = dict(kwargs)
        row["id"] = f"doc-{len(self.documents) + 1}"
        self.documents.append(row)
        return _record(row)

    async def archive_active_documents(self, workspace_id: str, document_key: str) -> None:
        for row in self.documents:
            if (
                row["workspace_id"] == workspace_id
                and row["document_key"] == document_key
                and row["status"] == "active"
            ):
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
        for index, section in enumerate(kwargs["sections"]):
            row = {
                "id": f"section-{len(self.sections) + 1}",
                "document_id": kwargs["document_id"],
                "workspace_id": kwargs["workspace_id"],
                "section_index": section.section_index,
                "heading": section.heading,
                "content": section.content,
                "section_embedding": kwargs["embeddings"][index],
            }
            self.sections.append(row)
            records.append(SectionRecord(id=row["id"], section_index=section.section_index))
        return records

    async def create_chunks(self, **kwargs: object) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for index, chunk in enumerate(kwargs["chunks"]):
            row = {
                "id": f"chunk-{len(self.chunks) + 1}",
                "document_id": kwargs["document_id"],
                "workspace_id": kwargs["workspace_id"],
                "section_id": kwargs["section_ids_by_index"][chunk.section_index],
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "heading": chunk.heading,
                "embedding": kwargs["embeddings"][index],
                "token_count": chunk.token_count,
            }
            self.chunks.append(row)
            rows.append(row)
        return rows

    async def refresh_term_statistics(self, workspace_id: str) -> int:
        self.term_refreshes.append(workspace_id)
        return len(self.active_chunks())

    def active_documents(self) -> list[dict[str, Any]]:
        return [row for row in self.documents if row["status"] == "active"]

    def active_chunks(self) -> list[dict[str, Any]]:
        active_ids = {row["id"] for row in self.active_documents()}
        return [row for row in self.chunks if row["document_id"] in active_ids]


def test_reingest_same_file_with_stale_metadata_replaces_active_chunks(tmp_path: Path) -> None:
    material = _write_material(tmp_path, "Use npx n8n to start the local server.")
    repository = FakeRepository()
    stale_title = "\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0444\u0430\u0439\u043b\u0430:"
    repository.documents.append(
        {
            "id": "doc-old",
            "workspace_id": "workspace-1",
            "document_key": material.name,
            "version": 1,
            "status": "active",
            "content_hash": file_content_hash(material),
            "metadata": {},
            "title": stale_title,
        }
    )
    repository.chunks.append(
        {
            "id": "chunk-old",
            "document_id": "doc-old",
            "workspace_id": "workspace-1",
            "content": f"Source file: {material.name}\nOld dirty chunk.",
        }
    )

    service = IndexingService(repository=repository, embedding_client=FakeEmbeddingClient())
    result = asyncio.run(service.ingest_file(material, workspace="team"))

    assert not result.skipped
    assert result.version == 2
    assert repository.documents[0]["status"] == "archived"
    assert [row["id"] for row in repository.active_documents()] == [result.document_id]
    assert repository.active_chunks()
    assert all("Source file:" not in str(row["content"]) for row in repository.active_chunks())
    active_document = repository.active_documents()[0]
    assert active_document["title"] != stale_title
    assert not str(active_document["title"]).casefold().startswith("source file")
    assert repository.term_refreshes == ["workspace-1"]


def test_reingest_after_signature_match_skips_without_duplicate_active_chunks(tmp_path: Path) -> None:
    material = _write_material(tmp_path, "Run n8n locally and open http://localhost:5678.")
    repository = FakeRepository()
    service = IndexingService(repository=repository, embedding_client=FakeEmbeddingClient())

    first = asyncio.run(service.ingest_file(material, workspace="team"))
    active_chunks_after_first = len(repository.active_chunks())
    second = asyncio.run(service.ingest_file(material, workspace="team"))

    assert not first.skipped
    assert second.skipped
    assert second.document_id == first.document_id
    assert len(repository.documents) == 1
    assert len(repository.active_documents()) == 1
    assert len(repository.active_chunks()) == active_chunks_after_first


def test_changed_same_document_key_archives_old_version_from_active_search(tmp_path: Path) -> None:
    material = _write_material(tmp_path, "Old install note that should leave active search.")
    repository = FakeRepository()
    service = IndexingService(repository=repository, embedding_client=FakeEmbeddingClient())

    first = asyncio.run(service.ingest_file(material, workspace="team"))
    material = _write_material(tmp_path, "New install note with port 5678 for active search.")
    second = asyncio.run(service.ingest_file(material, workspace="team"))

    assert not first.skipped
    assert not second.skipped
    assert len(repository.documents) == 2
    assert [row["id"] for row in repository.active_documents()] == [second.document_id]
    active_text = "\n".join(str(row["content"]) for row in repository.active_chunks())
    assert "New install note" in active_text
    assert "Old install note" not in active_text


def _write_material(tmp_path: Path, body: str) -> Path:
    material = tmp_path / "CLn02_text_double_deep.txt"
    material.write_text(
        "\n".join(
            [
                "Source file: CLn02_text_double_deep.txt",
                "# n8n local install",
                "",
                body,
                "",
                "## Check",
                "",
                "Verify the UI in the browser.",
            ]
        ),
        encoding="utf-8",
    )
    return material


def _record(row: dict[str, Any]) -> DocumentRecord:
    return DocumentRecord(
        id=str(row["id"]),
        version=int(row["version"]),
        status=str(row["status"]),
        content_hash=str(row["content_hash"]),
        metadata=row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
    )
