"""Index extracted external docs into the existing evidence-first tables."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import re
from urllib.parse import urlparse

from app.db.repositories import DocumentRecord
from app.external_docs.chunk_quality import is_low_value_external_chunk
from app.external_docs.types import EXTERNAL_DOCS_VERSION, ExternalDocSource, ExternalDocsIndexResult, ExtractedPage
from app.ingestion.chunker import ChunkDraft, ParentChildChunker, SectionDraft
from app.ingestion.document_cards import DocumentCardBuilder
from app.ingestion.indexing import EmbeddingClient
from app.ingestion.loaders import LoadedDocument, LoadedPage


class ExternalDocsRepositoryProtocol:
    """Repository methods needed for external docs indexing."""

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
        """Activate a document."""

    async def create_document_card(self, **kwargs: object) -> dict[str, object]:
        """Create a card."""

    async def create_sections(self, **kwargs: object) -> list[object]:
        """Create sections."""

    async def create_chunks(self, **kwargs: object) -> list[dict[str, object]]:
        """Create chunks."""

    async def refresh_term_statistics(self, workspace_id: str) -> int:
        """Refresh term statistics."""


EXTERNAL_CHUNK_QUALITY_VERSION = "external-chunk-quality-v1"


class ExternalDocsIndexer:
    """Store cleaned external docs through the same evidence tables as local materials."""

    def __init__(
        self,
        *,
        repository: ExternalDocsRepositoryProtocol,
        embedding_client: EmbeddingClient,
        chunker: ParentChildChunker | None = None,
        card_builder: DocumentCardBuilder | None = None,
    ) -> None:
        self._repository = repository
        self._embedding_client = embedding_client
        self._chunker = chunker or ParentChildChunker()
        self._card_builder = card_builder or DocumentCardBuilder()

    async def index_page(
        self,
        page: ExtractedPage,
        source: ExternalDocSource,
        *,
        workspace: str = "team",
    ) -> ExternalDocsIndexResult:
        """Index one extracted page and archive stale active versions."""
        workspace_row = await self._repository.get_or_create_workspace(workspace)
        workspace_id = str(workspace_row["id"])
        document_key = page.canonical_url or page.source_url
        active = await self._repository.get_active_document(workspace_id, document_key)
        signature = _external_signature(page)
        if (
            active is not None
            and active.content_hash == page.content_hash
            and _active_matches_external_signature(active, signature)
        ):
            return ExternalDocsIndexResult(
                source_name=source.name,
                url=page.source_url,
                document_id=active.id,
                document_key=document_key,
                version=active.version,
                skipped=True,
            )

        latest = await self._repository.get_latest_document(workspace_id, document_key)
        version = (latest.version + 1) if latest else 1
        loaded = _loaded_document(page, source)
        external_metadata = _external_metadata(page, source, signature=signature)

        sections = tuple(_section_with_metadata(section, external_metadata) for section in self._chunker.split_sections(loaded))
        raw_chunks = tuple(_chunk_with_metadata(chunk, external_metadata) for chunk in self._chunker.split_chunks(sections))
        chunks = _filter_low_value_chunks(raw_chunks)
        card = await self._card_builder.build(loaded, sections)
        card = replace(
            card,
            metadata={
                **card.metadata,
                **external_metadata,
                "content_type": ["official_docs", "external_docs"],
                "content_types": ["official_docs", "external_docs"],
            },
        )

        document = await self._repository.create_document(
            workspace_id=workspace_id,
            source_type="external_docs",
            filename=loaded.filename,
            document_key=document_key,
            title=card.title or loaded.title,
            course=None,
            module=source.name,
            lesson=None,
            version=version,
            status="draft",
            content_hash=page.content_hash,
            metadata=external_metadata,
        )

        card_embedding = await self._embedding_client.embed(card.to_embedding_text())
        section_embeddings = await self._embedding_client.embed_many(
            [
                "\n".join(part for part in (section.heading, section.summary or "", section.content[:2400]) if part)
                for section in sections
            ]
        )
        chunk_embeddings = await self._embedding_client.embed_many([chunk.content for chunk in chunks])

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

        archived_old = active is not None
        await self._repository.archive_active_documents(workspace_id, document_key)
        await self._repository.activate_document(document.id)
        await self._refresh_term_statistics(workspace_id)

        return ExternalDocsIndexResult(
            source_name=source.name,
            url=page.source_url,
            document_id=document.id,
            document_key=document_key,
            version=version,
            skipped=False,
            archived_old=archived_old,
            sections_count=len(sections),
            chunks_count=len(chunks),
        )

    async def _refresh_term_statistics(self, workspace_id: str) -> None:
        refresh = getattr(self._repository, "refresh_term_statistics", None)
        if refresh is None:
            return
        try:
            await refresh(workspace_id)
        except Exception:
            return


def _loaded_document(page: ExtractedPage, source: ExternalDocSource) -> LoadedDocument:
    filename = _filename_from_url(page.canonical_url)
    return LoadedDocument(
        path=Path(filename),
        source_type="external_docs",
        filename=filename,
        title=page.title,
        structured_text=page.structured_text,
        pages=(LoadedPage(page_number=None, text=page.structured_text, metadata={"source_url": page.source_url}),),
        metadata={
            "source_kind": "external_docs",
            "source_name": source.name,
            "source_domain": _domain(page.canonical_url),
            "source_url": page.source_url,
            "canonical_url": page.canonical_url,
            "content_type": ["official_docs", "external_docs"],
        },
    )


def _external_metadata(page: ExtractedPage, source: ExternalDocSource, *, signature: str) -> dict[str, object]:
        return {
        **page.metadata,
        "source_kind": "external_docs",
        "source_name": source.name,
        "source_domain": _domain(page.canonical_url),
        "source_url": page.source_url,
        "source_uri": page.canonical_url,
        "canonical_url": page.canonical_url,
        "crawled_at": page.crawled_at.isoformat(),
        "content_hash": page.content_hash,
        "freshness_status": "fresh",
        "external_docs_version": EXTERNAL_DOCS_VERSION,
        "external_chunk_quality_version": EXTERNAL_CHUNK_QUALITY_VERSION,
        "content_type": ["official_docs", "external_docs"],
        "content_types": ["official_docs", "external_docs"],
        "ingestion": {
            "pipeline_version": EXTERNAL_DOCS_VERSION,
            "signature": signature,
            "signature_source": "external_structured_text",
        },
    }


def _section_with_metadata(section: SectionDraft, metadata: dict[str, object]) -> SectionDraft:
    return replace(section, metadata={**section.metadata, **metadata})


def _chunk_with_metadata(chunk: ChunkDraft, metadata: dict[str, object]) -> ChunkDraft:
    return replace(chunk, metadata={**chunk.metadata, **metadata})


def _filter_low_value_chunks(chunks: tuple[ChunkDraft, ...]) -> tuple[ChunkDraft, ...]:
    kept: list[ChunkDraft] = []
    for chunk in chunks:
        if is_low_value_external_chunk(chunk.content, heading=chunk.heading):
            continue
        kept.append(replace(chunk, chunk_index=len(kept)))
    return tuple(kept) or chunks


def _external_signature(page: ExtractedPage) -> str:
    digest = sha256()
    for value in (
        EXTERNAL_DOCS_VERSION,
        EXTERNAL_CHUNK_QUALITY_VERSION,
        page.canonical_url,
        page.content_hash,
        page.structured_text,
    ):
        digest.update(b"\0")
        digest.update(str(value or "").encode("utf-8", errors="replace"))
    return digest.hexdigest()


def _active_matches_external_signature(active: DocumentRecord, signature: str) -> bool:
    ingestion = active.metadata.get("ingestion") if isinstance(active.metadata, dict) else None
    if not isinstance(ingestion, dict):
        return False
    return ingestion.get("pipeline_version") == EXTERNAL_DOCS_VERSION and ingestion.get("signature") == signature


def _filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    slug = path.rsplit("/", 1)[-1] if path else parsed.hostname or "external-doc"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", slug).strip("-") or "external-doc"
    if "." not in slug:
        slug += ".html"
    return slug[:140]


def _domain(url: str) -> str:
    return (urlparse(url).hostname or "").lower()
