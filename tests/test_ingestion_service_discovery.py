import asyncio
from pathlib import Path
from typing import Any

from app.db.repositories import DocumentRecord, SectionRecord
from app.ingestion.indexing import IndexingService
from app.service_registry.detector import ServiceDetector
from app.service_registry.types import ServiceDefinition


class FakeEmbeddingClient:
    async def embed(self, text: str) -> list[float]:
        return (await self.embed_many([text]))[0]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [[float(index % 7) / 7 for index in range(1024)] for _ in texts]


class FakeRepository:
    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []
        self.cards: list[dict[str, Any]] = []
        self.sections: list[dict[str, Any]] = []
        self.chunks: list[dict[str, Any]] = []

    async def get_or_create_workspace(self, name: str) -> dict[str, Any]:
        return {"id": "workspace-1", "name": name}

    async def get_latest_document(self, workspace_id: str, document_key: str) -> DocumentRecord | None:
        rows = [
            row
            for row in self.documents
            if row["workspace_id"] == workspace_id and row["document_key"] == document_key
        ]
        if not rows:
            return None
        latest = max(rows, key=lambda row: int(row["version"]))
        return _document_record(latest)

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
        latest = max(rows, key=lambda row: int(row["version"]))
        return _document_record(latest)

    async def create_document(self, **kwargs: object) -> DocumentRecord:
        row = dict(kwargs)
        row["id"] = "doc-1"
        self.documents.append(row)
        return DocumentRecord(
            id="doc-1",
            version=int(row["version"]),
            status=str(row["status"]),
            content_hash=str(row["content_hash"]),
            metadata=row["metadata"] if isinstance(row.get("metadata"), dict) else {},
        )

    async def archive_active_documents(self, workspace_id: str, document_key: str) -> None:
        del workspace_id, document_key

    async def activate_document(self, document_id: str) -> None:
        for row in self.documents:
            if row["id"] == document_id:
                row["status"] = "active"

    async def create_document_card(self, **kwargs: object) -> dict[str, object]:
        self.cards.append(dict(kwargs))
        return dict(kwargs)

    async def create_sections(self, **kwargs: object) -> list[SectionRecord]:
        records: list[SectionRecord] = []
        for section in kwargs["sections"]:
            row = {
                "id": f"section-{len(self.sections) + 1}",
                "section_index": section.section_index,
                "metadata": section.metadata,
            }
            self.sections.append(row)
            records.append(SectionRecord(id=str(row["id"]), section_index=int(row["section_index"])))
        return records

    async def create_chunks(self, **kwargs: object) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for chunk in kwargs["chunks"]:
            row = {
                "id": f"chunk-{len(self.chunks) + 1}",
                "section_id": kwargs["section_ids_by_index"][chunk.section_index],
                "content": chunk.content,
                "metadata": chunk.metadata,
            }
            self.chunks.append(row)
            rows.append(row)
        return rows


def test_ingestion_adds_detected_service_ids_to_metadata(tmp_path: Path) -> None:
    material = tmp_path / "lesson.md"
    material.write_text(
        "# Example setup\n\nUse Example API credentials in this workflow.\n\n## Run\n\nCall Example from the app.",
        encoding="utf-8",
    )
    detector = ServiceDetector(
        (
            ServiceDefinition(
                service_id="example",
                display_name="Example",
                aliases=("example",),
                docs_source=None,
                status="not_configured",
            ),
        )
    )
    repository = FakeRepository()
    service = IndexingService(
        repository=repository,
        embedding_client=FakeEmbeddingClient(),
        service_detector=detector,
    )

    result = asyncio.run(service.ingest_file(material, workspace="team"))

    assert repository.documents[0]["metadata"]["service_ids"] == ["example"]
    assert repository.cards[0]["card"].metadata["service_ids"] == ["example"]
    assert any(row["metadata"].get("service_ids") == ["example"] for row in repository.sections)
    assert any(row["metadata"].get("service_ids") == ["example"] for row in repository.chunks)
    assert result.service_ids == ("example",)
    assert result.service_mentions[0]["service_id"] == "example"
    assert result.service_mentions[0]["display_name"] == "Example"


def test_skipped_ingestion_result_keeps_service_metadata(tmp_path: Path) -> None:
    material = tmp_path / "lesson.md"
    material.write_text("# Example setup\n\nUse Example API credentials.", encoding="utf-8")
    detector = ServiceDetector(
        (
            ServiceDefinition(
                service_id="example",
                display_name="Example",
                aliases=("example",),
                docs_source=None,
                status="not_configured",
            ),
        )
    )
    repository = FakeRepository()
    service = IndexingService(
        repository=repository,
        embedding_client=FakeEmbeddingClient(),
        service_detector=detector,
    )

    first = asyncio.run(service.ingest_file(material, workspace="team"))
    second = asyncio.run(service.ingest_file(material, workspace="team"))

    assert first.skipped is False
    assert second.skipped is True
    assert second.service_ids == ("example",)
    assert second.service_mentions[0]["display_name"] == "Example"


def _document_record(row: dict[str, Any]) -> DocumentRecord:
    return DocumentRecord(
        id=str(row["id"]),
        version=int(row["version"]),
        status=str(row["status"]),
        content_hash=str(row["content_hash"]),
        metadata=row["metadata"] if isinstance(row.get("metadata"), dict) else {},
    )
