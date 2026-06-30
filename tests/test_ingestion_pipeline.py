import asyncio
import tempfile
from pathlib import Path
from typing import Any

from app.db.repositories import DocumentRecord, SectionRecord
from app.ingestion.indexing import IndexingService


class FakeEmbeddingClient:
    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim
        self.texts: list[str] = []

    async def embed(self, text: str) -> list[float]:
        return (await self.embed_many([text]))[0]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return [[float((index % 17) / 17) for index in range(self.dim)] for _ in texts]


class FakeRepository:
    def __init__(self) -> None:
        self.workspace = {"id": "workspace-1", "name": "team"}
        self.documents: list[dict[str, Any]] = []
        self.cards: list[dict[str, Any]] = []
        self.sections: list[dict[str, Any]] = []
        self.chunks: list[dict[str, Any]] = []

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
        sections = kwargs["sections"]
        embeddings = kwargs["embeddings"]
        records: list[SectionRecord] = []
        for index, section in enumerate(sections):
            row = {
                "id": f"section-{len(self.sections) + 1}",
                "document_id": kwargs["document_id"],
                "workspace_id": kwargs["workspace_id"],
                "section_index": section.section_index,
                "heading": section.heading,
                "section_embedding": embeddings[index],
            }
            self.sections.append(row)
            records.append(SectionRecord(id=row["id"], section_index=section.section_index))
        return records

    async def create_chunks(self, **kwargs: object) -> list[dict[str, object]]:
        chunks = kwargs["chunks"]
        section_ids_by_index = kwargs["section_ids_by_index"]
        embeddings = kwargs["embeddings"]
        rows: list[dict[str, object]] = []
        for index, chunk in enumerate(chunks):
            row = {
                "id": f"chunk-{len(self.chunks) + 1}",
                "document_id": kwargs["document_id"],
                "section_id": section_ids_by_index[chunk.section_index],
                "workspace_id": kwargs["workspace_id"],
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "heading": chunk.heading,
                "embedding": embeddings[index],
                "token_count": chunk.token_count,
            }
            self.chunks.append(row)
            rows.append(row)
        return rows


def test_ingestion_creates_card_sections_chunks_and_embeddings() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        material = Path(tmpdir) / "lesson.md"
        material.write_text(
            "\n".join(
                [
                    "# Supabase API в n8n",
                    "",
                    "Материал объясняет, как подключить Supabase API в n8n.",
                    "",
                    "## Настройка credentials",
                    "",
                    "Создайте HTTP credentials и сохраните ключ только на сервере.",
                    "",
                    "## Проверка запроса",
                    "",
                    "Отправьте тестовый запрос и проверьте код ответа.",
                ]
            ),
            encoding="utf-8",
        )

        repository = FakeRepository()
        service = IndexingService(repository=repository, embedding_client=FakeEmbeddingClient())
        result = asyncio.run(service.ingest_file(material, workspace="team", course="n8n 3.0"))

        assert not result.skipped
        assert len(repository.documents) == 1
        assert len(repository.cards) == 1
        assert len(repository.sections) >= 2
        assert len(repository.chunks) >= 2
        assert repository.cards[0]["card"].questions_answered
        assert len(repository.cards[0]["embedding"]) == 1024
        assert all(len(row["section_embedding"]) == 1024 for row in repository.sections)
        assert all(len(row["embedding"]) == 1024 for row in repository.chunks)

        section_ids = {row["id"] for row in repository.sections}
        assert all(row["section_id"] in section_ids for row in repository.chunks)


def test_repeated_same_file_is_skipped_without_duplicate_rows() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        material = Path(tmpdir) / "lesson.md"
        material.write_text("# Lesson\n\nSame content.\n\n## Topic\n\nDetails.", encoding="utf-8")

        repository = FakeRepository()
        service = IndexingService(repository=repository, embedding_client=FakeEmbeddingClient())

        first = asyncio.run(service.ingest_file(material, workspace="team"))
        second = asyncio.run(service.ingest_file(material, workspace="team"))

        assert not first.skipped
        assert second.skipped
        assert second.document_id == first.document_id
        assert len(repository.documents) == 1
        assert len(repository.cards) == 1
        assert len(repository.sections) >= 1
        assert len(repository.chunks) >= 1


def _record(row: dict[str, Any]) -> DocumentRecord:
    return DocumentRecord(
        id=str(row["id"]),
        version=int(row["version"]),
        status=str(row["status"]),
        content_hash=str(row["content_hash"]),
        metadata=row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
    )
