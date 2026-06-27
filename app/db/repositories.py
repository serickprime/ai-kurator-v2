"""Repository layer for Supabase access."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.ingestion.chunker import ChunkDraft, SectionDraft
from app.ingestion.document_cards import DocumentCard

if TYPE_CHECKING:
    from app.db.supabase_client import SupabaseClient


@dataclass(frozen=True)
class DocumentRecord:
    """Stored document snapshot."""

    id: str
    version: int
    status: str
    content_hash: str


@dataclass(frozen=True)
class SectionRecord:
    """Stored section snapshot."""

    id: str
    section_index: int


class DocumentRepository:
    """Database access for documents, cards, sections, and chunks."""

    def __init__(self, client: "SupabaseClient") -> None:
        self._client = client

    async def get_or_create_workspace(self, name: str) -> dict[str, Any]:
        """Return an existing workspace by name or create it."""
        rows = await self._client.select(
            "workspaces",
            params={"select": "id,name", "name": f"eq.{name}", "limit": "1"},
        )
        if rows:
            return rows[0]
        return (await self._client.insert("workspaces", {"name": name}))[0]

    async def get_latest_document(
        self,
        workspace_id: str,
        document_key: str,
    ) -> DocumentRecord | None:
        """Return latest document version for a document key."""
        rows = await self._client.select(
            "documents",
            params={
                "select": "id,version,status,content_hash",
                "workspace_id": f"eq.{workspace_id}",
                "document_key": f"eq.{document_key}",
                "order": "version.desc",
                "limit": "1",
            },
        )
        return _document_record(rows[0]) if rows else None

    async def get_active_document(
        self,
        workspace_id: str,
        document_key: str,
    ) -> DocumentRecord | None:
        """Return active document for a document key."""
        rows = await self._client.select(
            "documents",
            params={
                "select": "id,version,status,content_hash",
                "workspace_id": f"eq.{workspace_id}",
                "document_key": f"eq.{document_key}",
                "status": "eq.active",
                "order": "version.desc",
                "limit": "1",
            },
        )
        return _document_record(rows[0]) if rows else None

    async def create_document(
        self,
        *,
        workspace_id: str,
        source_type: str,
        filename: str,
        document_key: str,
        title: str,
        course: str | None,
        module: str | None,
        lesson: str | None,
        version: int,
        status: str,
        content_hash: str,
        metadata: dict[str, Any],
    ) -> DocumentRecord:
        """Create one document row."""
        row = {
            "workspace_id": workspace_id,
            "source_type": source_type,
            "filename": filename,
            "document_key": document_key,
            "title": title,
            "course": course,
            "module": module,
            "lesson": lesson,
            "version": version,
            "status": status,
            "content_hash": content_hash,
            "metadata": metadata,
        }
        return _document_record((await self._client.insert("documents", row))[0])

    async def archive_active_documents(self, workspace_id: str, document_key: str) -> None:
        """Archive active versions for a document key."""
        await self._client.update(
            "documents",
            {"status": "archived"},
            params={
                "workspace_id": f"eq.{workspace_id}",
                "document_key": f"eq.{document_key}",
                "status": "eq.active",
            },
        )

    async def activate_document(self, document_id: str) -> None:
        """Mark a draft document active."""
        await self._client.update("documents", {"status": "active"}, params={"id": f"eq.{document_id}"})

    async def create_document_card(
        self,
        *,
        document_id: str,
        workspace_id: str,
        card: DocumentCard,
        embedding: list[float],
    ) -> dict[str, Any]:
        """Store a document card."""
        row = {
            "document_id": document_id,
            "workspace_id": workspace_id,
            "summary": card.summary,
            "topics": list(card.topics),
            "questions_answered": list(card.questions_answered),
            "entities": list(card.entities),
            "task_types": list(card.task_types),
            "not_about": list(card.not_about),
            "quality_score": card.quality_score,
            "card_embedding": embedding,
            "metadata": card.metadata,
        }
        return (await self._client.insert("document_cards", row))[0]

    async def create_sections(
        self,
        *,
        document_id: str,
        workspace_id: str,
        sections: tuple[SectionDraft, ...],
        embeddings: list[list[float]],
    ) -> list[SectionRecord]:
        """Store section rows and return ids by section index."""
        rows = [
            {
                "document_id": document_id,
                "workspace_id": workspace_id,
                "section_index": section.section_index,
                "heading": section.heading,
                "summary": section.summary,
                "page_start": section.page_start,
                "page_end": section.page_end,
                "metadata": section.metadata,
                "section_embedding": embeddings[index],
            }
            for index, section in enumerate(sections)
        ]
        inserted = await self._client.insert("sections", rows)
        return [SectionRecord(id=row["id"], section_index=int(row["section_index"])) for row in inserted]

    async def create_chunks(
        self,
        *,
        document_id: str,
        workspace_id: str,
        chunks: tuple[ChunkDraft, ...],
        section_ids_by_index: dict[int, str],
        embeddings: list[list[float]],
        batch_size: int = 100,
    ) -> list[dict[str, Any]]:
        """Store chunk rows in batches."""
        all_rows = [
            {
                "document_id": document_id,
                "section_id": section_ids_by_index[chunk.section_index],
                "workspace_id": workspace_id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "embedding": embeddings[index],
                "token_count": chunk.token_count,
                "page": chunk.page,
                "heading": chunk.heading,
                "metadata": chunk.metadata,
            }
            for index, chunk in enumerate(chunks)
        ]

        inserted: list[dict[str, Any]] = []
        for start in range(0, len(all_rows), batch_size):
            inserted.extend(await self._client.insert("chunks", all_rows[start : start + batch_size]))
        return inserted


class ConversationRepository:
    """Database access for Telegram conversations and messages."""

    def __init__(self, client: "SupabaseClient") -> None:
        self._client = client


class EvidenceLogRepository:
    """Database access for evidence-first RAG traces."""

    def __init__(self, client: "SupabaseClient") -> None:
        self._client = client

    async def log_evidence(
        self,
        *,
        workspace_id: str,
        question: str,
        question_analysis: dict[str, object],
        document_candidates: list[dict[str, object]],
        evidence_pack: dict[str, object],
        final_answer: str,
        final_sources: list[str],
    ) -> None:
        """Store one pipeline trace in evidence_logs."""
        await self._client.insert(
            "evidence_logs",
            {
                "workspace_id": workspace_id,
                "question": question,
                "question_analysis": question_analysis,
                "document_candidates": document_candidates,
                "evidence_pack": evidence_pack,
                "final_answer": final_answer,
                "final_sources": final_sources,
            },
        )


def _document_record(row: dict[str, Any]) -> DocumentRecord:
    return DocumentRecord(
        id=str(row["id"]),
        version=int(row["version"]),
        status=str(row["status"]),
        content_hash=str(row["content_hash"]),
    )
