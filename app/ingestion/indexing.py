"""Indexing orchestration for evidence-first ingestion."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.db.repositories import DocumentRecord, DocumentRepository
from app.ingestion.chunker import ChunkDraft, ParentChildChunker, SectionDraft
from app.ingestion.document_cards import DocumentCardBuilder
from app.ingestion.loaders import FileLoader, LoadedDocument, is_supported_file


class EmbeddingClient(Protocol):
    """Embedding adapter required by indexing."""

    async def embed(self, text: str) -> list[float]:
        """Embed one text string."""

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Embed many text strings."""


class IngestionRepository(Protocol):
    """Repository methods required by the indexing service."""

    async def get_or_create_workspace(self, name: str) -> dict[str, object]:
        """Return or create a workspace."""

    async def get_latest_document(self, workspace_id: str, document_key: str) -> DocumentRecord | None:
        """Return latest document version."""

    async def get_active_document(self, workspace_id: str, document_key: str) -> DocumentRecord | None:
        """Return active document version."""

    async def create_document(self, **kwargs: object) -> DocumentRecord:
        """Create a document."""

    async def archive_active_documents(self, workspace_id: str, document_key: str) -> None:
        """Archive active documents."""

    async def activate_document(self, document_id: str) -> None:
        """Activate a draft document."""

    async def create_document_card(self, **kwargs: object) -> dict[str, object]:
        """Create a document card."""

    async def create_sections(self, **kwargs: object) -> list[object]:
        """Create sections."""

    async def create_chunks(self, **kwargs: object) -> list[dict[str, object]]:
        """Create chunks."""


@dataclass(frozen=True)
class IngestionResult:
    """Result of ingesting one file."""

    path: Path
    document_id: str
    document_key: str
    version: int
    skipped: bool
    sections_count: int = 0
    chunks_count: int = 0
    content_hash: str = ""


class IndexingService:
    """Coordinates loading, card creation, embeddings, and Supabase storage."""

    def __init__(
        self,
        *,
        repository: IngestionRepository,
        embedding_client: EmbeddingClient,
        loader: FileLoader | None = None,
        chunker: ParentChildChunker | None = None,
        card_builder: DocumentCardBuilder | None = None,
    ) -> None:
        self._repository = repository
        self._embedding_client = embedding_client
        self._loader = loader or FileLoader()
        self._chunker = chunker or ParentChildChunker()
        self._card_builder = card_builder or DocumentCardBuilder()

    async def ingest_path(
        self,
        path: Path,
        *,
        workspace: str = "team",
        course: str | None = None,
        module: str | None = None,
        lesson: str | None = None,
    ) -> list[IngestionResult]:
        """Ingest a file or all supported files under a directory."""
        path = path.resolve()
        if path.is_dir():
            files = sorted(item for item in path.rglob("*") if is_supported_file(item))
            return [
                await self.ingest_file(
                    file_path,
                    workspace=workspace,
                    course=course,
                    module=module,
                    lesson=lesson,
                    document_key=_document_key(file_path, base_path=path),
                )
                for file_path in files
            ]

        return [
            await self.ingest_file(
                path,
                workspace=workspace,
                course=course,
                module=module,
                lesson=lesson,
            )
        ]

    async def ingest_file(
        self,
        path: Path,
        *,
        workspace: str = "team",
        course: str | None = None,
        module: str | None = None,
        lesson: str | None = None,
        document_key: str | None = None,
    ) -> IngestionResult:
        """Ingest one file into Supabase."""
        path = path.resolve()
        loaded = await self._loader.load(path)
        content_hash = file_content_hash(path)
        key = document_key or _document_key(path)

        workspace_row = await self._repository.get_or_create_workspace(workspace)
        workspace_id = str(workspace_row["id"])

        active = await self._repository.get_active_document(workspace_id, key)
        if active is not None and active.content_hash == content_hash:
            return IngestionResult(
                path=path,
                document_id=active.id,
                document_key=key,
                version=active.version,
                skipped=True,
                content_hash=content_hash,
            )

        latest = await self._repository.get_latest_document(workspace_id, key)
        version = (latest.version + 1) if latest else 1

        sections = self._chunker.split_sections(loaded)
        chunks = self._chunker.split_chunks(sections)
        card = await self._card_builder.build(loaded, sections)

        document = await self._repository.create_document(
            workspace_id=workspace_id,
            source_type=loaded.source_type,
            filename=loaded.filename,
            document_key=key,
            title=card.title or loaded.title,
            course=course,
            module=module,
            lesson=lesson,
            version=version,
            status="draft",
            content_hash=content_hash,
            metadata=_document_metadata(loaded),
        )

        card_embedding = await self._embedding_client.embed(card.to_embedding_text())
        section_embeddings = await self._embed_sections(sections)
        chunk_embeddings = await self._embed_chunks(chunks)

        await self._repository.create_document_card(
            document_id=document.id,
            workspace_id=workspace_id,
            card=card,
            embedding=card_embedding,
        )
        section_records = await self._repository.create_sections(
            document_id=document.id,
            workspace_id=workspace_id,
            sections=sections,
            embeddings=section_embeddings,
        )
        section_ids_by_index = {
            int(getattr(record, "section_index")): str(getattr(record, "id")) for record in section_records
        }
        await self._repository.create_chunks(
            document_id=document.id,
            workspace_id=workspace_id,
            chunks=chunks,
            section_ids_by_index=section_ids_by_index,
            embeddings=chunk_embeddings,
        )

        await self._repository.archive_active_documents(workspace_id, key)
        await self._repository.activate_document(document.id)

        return IngestionResult(
            path=path,
            document_id=document.id,
            document_key=key,
            version=version,
            skipped=False,
            sections_count=len(sections),
            chunks_count=len(chunks),
            content_hash=content_hash,
        )

    async def _embed_sections(self, sections: tuple[SectionDraft, ...]) -> list[list[float]]:
        texts = [
            "\n".join(
                part
                for part in (
                    section.heading,
                    section.summary or "",
                    section.content[:2400],
                )
                if part
            )
            for section in sections
        ]
        return await self._embedding_client.embed_many(texts)

    async def _embed_chunks(self, chunks: tuple[ChunkDraft, ...]) -> list[list[float]]:
        return await self._embedding_client.embed_many([chunk.content for chunk in chunks])


def file_content_hash(path: Path) -> str:
    """Return SHA-256 hash for a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _document_key(path: Path, base_path: Path | None = None) -> str:
    if base_path is None:
        return path.name
    return path.resolve().relative_to(base_path.resolve()).as_posix()


def _document_metadata(document: LoadedDocument) -> dict[str, object]:
    return {
        "path": str(document.path),
        "source_type": document.source_type,
        "loader": document.metadata,
        "page_count": len(document.pages),
    }


def build_default_indexing_service(
    repository: DocumentRepository,
    embedding_client: EmbeddingClient,
    loader: FileLoader | None = None,
    card_builder: DocumentCardBuilder | None = None,
) -> IndexingService:
    """Build the default indexing service."""
    return IndexingService(
        repository=repository,
        embedding_client=embedding_client,
        loader=loader,
        card_builder=card_builder,
    )
