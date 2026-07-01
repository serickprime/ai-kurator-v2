"""Repository layer for Supabase access."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.ingestion.chunker import ChunkDraft, SectionDraft
from app.ingestion.document_cards import DocumentCard
from app.db.supabase_client import SupabaseRequestError

if TYPE_CHECKING:
    from app.db.supabase_client import SupabaseClient

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentRecord:
    """Stored document snapshot."""

    id: str
    version: int
    status: str
    content_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SectionRecord:
    """Stored section snapshot."""

    id: str
    section_index: int


@dataclass(frozen=True)
class UserSettings:
    """Per-user Telegram UX and model settings."""

    telegram_user_id: int
    answer_mode: str = "cheap"
    vision_mode: str = "auto"
    debug_mode: bool = False
    selected_workspace_id: str | None = None
    updated_at: datetime | None = None

    def to_payload(self) -> dict[str, Any]:
        """Serialize settings for Supabase."""
        return {
            "telegram_user_id": self.telegram_user_id,
            "answer_mode": self.answer_mode,
            "vision_mode": self.vision_mode,
            "debug_mode": self.debug_mode,
            "selected_workspace_id": self.selected_workspace_id,
            "updated_at": (self.updated_at or datetime.now(timezone.utc)).isoformat(),
        }


class DocumentRepository:
    """Database access for documents, cards, sections, and chunks."""

    def __init__(self, client: "SupabaseClient") -> None:
        self._client = client
        self._term_statistics_available: bool | None = None

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
                "select": "id,version,status,content_hash,metadata",
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
                "select": "id,version,status,content_hash,metadata",
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

    async def refresh_term_statistics(self, workspace_id: str) -> int:
        """Rebuild corpus term statistics for active documents in a workspace."""
        if self._term_statistics_available is False:
            return -1
        try:
            rows = await self._client.rpc("refresh_term_statistics", {"p_workspace_id": workspace_id})
        except SupabaseRequestError as exc:
            if exc.is_missing_relation:
                self._term_statistics_available = False
                LOGGER.info("term_statistics is unavailable; ingestion will use fallback term scoring")
                return -1
            LOGGER.warning("failed to refresh term statistics for workspace %s: %s", workspace_id, exc)
            return -2
        except Exception as exc:  # noqa: BLE001 - term stats must not break ingestion
            LOGGER.warning("failed to refresh term statistics for workspace %s: %s", workspace_id, exc)
            return -2
        self._term_statistics_available = True
        if not rows:
            return 0
        value = next(iter(rows[0].values()), 0)
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    async def list_term_statistics(self, workspace_id: str, limit: int = 5000) -> list[dict[str, Any]]:
        """Return corpus term statistics for workspace-level rarity scoring."""
        if self._term_statistics_available is False:
            return []
        try:
            rows = await self._client.select(
                "term_statistics",
                params={
                    "select": (
                        "term,normalized_term,document_frequency,chunk_frequency,course_frequency,"
                        "first_seen_at,last_seen_at,examples,term_type_guess,metadata"
                    ),
                    "workspace_id": f"eq.{workspace_id}",
                    "order": "document_frequency.desc",
                    "limit": str(limit),
                },
            )
        except SupabaseRequestError as exc:
            if exc.is_missing_relation:
                self._term_statistics_available = False
                LOGGER.info("term_statistics is unavailable; using fallback term scoring")
                return []
            raise
        self._term_statistics_available = True
        return rows


class ConversationRepository:
    """Database access for Telegram conversations and messages."""

    def __init__(self, client: "SupabaseClient") -> None:
        self._client = client

    async def get_active_conversation(self, telegram_user_id: int, workspace_id: str) -> dict[str, Any] | None:
        """Return active conversation for a Telegram user."""
        rows = await self._client.select(
            "conversations",
            params={
                "select": "id,title,summary,is_active",
                "telegram_user_id": f"eq.{telegram_user_id}",
                "workspace_id": f"eq.{workspace_id}",
                "is_active": "eq.true",
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    async def close_active_conversations(self, telegram_user_id: int, workspace_id: str) -> None:
        """Close active conversations for a Telegram user."""
        await self._client.update(
            "conversations",
            {"is_active": False},
            params={
                "telegram_user_id": f"eq.{telegram_user_id}",
                "workspace_id": f"eq.{workspace_id}",
                "is_active": "eq.true",
            },
        )

    async def create_conversation(
        self,
        *,
        telegram_user_id: int,
        workspace_id: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Create a new active conversation."""
        rows = await self._client.insert(
            "conversations",
            {
                "telegram_user_id": telegram_user_id,
                "workspace_id": workspace_id,
                "title": title,
                "is_active": True,
            },
        )
        return rows[0]


class BotUserRepository:
    """Database access for Telegram bot users."""

    def __init__(self, client: "SupabaseClient") -> None:
        self._client = client

    async def ensure_user(self, telegram_user_id: int, role: str = "user") -> dict[str, Any]:
        """Create a bot user when missing and return the row."""
        rows = await self._client.select(
            "bot_users",
            params={
                "select": "telegram_user_id,role,is_active",
                "telegram_user_id": f"eq.{telegram_user_id}",
                "limit": "1",
            },
        )
        if rows:
            return rows[0]
        return (
            await self._client.insert(
                "bot_users",
                {"telegram_user_id": telegram_user_id, "role": role, "is_active": True},
            )
        )[0]


class UserSettingsRepository:
    """Database access for user_settings.

    This repository expects the optional `public.user_settings` table described in
    the Telegram UX migration proposal. It is intentionally isolated so the RAG
    pipeline and schema remain untouched until that migration is approved.
    """

    def __init__(self, client: "SupabaseClient") -> None:
        self._client = client

    async def get(self, telegram_user_id: int) -> UserSettings:
        """Return settings for a user or defaults when no row exists."""
        rows = await self._client.select(
            "user_settings",
            params={
                "select": "telegram_user_id,answer_mode,vision_mode,debug_mode,selected_workspace_id,updated_at",
                "telegram_user_id": f"eq.{telegram_user_id}",
                "limit": "1",
            },
        )
        if not rows:
            return UserSettings(telegram_user_id=telegram_user_id)
        return _user_settings(rows[0])

    async def save(self, settings: UserSettings) -> UserSettings:
        """Insert or update settings for a user."""
        existing = await self._client.select(
            "user_settings",
            params={"select": "telegram_user_id", "telegram_user_id": f"eq.{settings.telegram_user_id}", "limit": "1"},
        )
        payload = settings.to_payload()
        if existing:
            await self._client.update(
                "user_settings",
                payload,
                params={"telegram_user_id": f"eq.{settings.telegram_user_id}"},
            )
        else:
            await self._client.insert("user_settings", payload)
        return settings

    async def set_answer_mode(self, telegram_user_id: int, answer_mode: str) -> UserSettings:
        """Update only answer mode."""
        current = await self.get(telegram_user_id)
        updated = UserSettings(
            telegram_user_id=current.telegram_user_id,
            answer_mode=answer_mode,
            vision_mode=current.vision_mode,
            debug_mode=current.debug_mode,
            selected_workspace_id=current.selected_workspace_id,
            updated_at=datetime.now(timezone.utc),
        )
        return await self.save(updated)

    async def set_vision_mode(self, telegram_user_id: int, vision_mode: str) -> UserSettings:
        """Update only vision mode."""
        current = await self.get(telegram_user_id)
        updated = UserSettings(
            telegram_user_id=current.telegram_user_id,
            answer_mode=current.answer_mode,
            vision_mode=vision_mode,
            debug_mode=current.debug_mode,
            selected_workspace_id=current.selected_workspace_id,
            updated_at=datetime.now(timezone.utc),
        )
        return await self.save(updated)

    async def set_debug_mode(self, telegram_user_id: int, debug_mode: bool) -> UserSettings:
        """Update only debug mode."""
        current = await self.get(telegram_user_id)
        updated = UserSettings(
            telegram_user_id=current.telegram_user_id,
            answer_mode=current.answer_mode,
            vision_mode=current.vision_mode,
            debug_mode=debug_mode,
            selected_workspace_id=current.selected_workspace_id,
            updated_at=datetime.now(timezone.utc),
        )
        return await self.save(updated)


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
    metadata = row.get("metadata")
    return DocumentRecord(
        id=str(row["id"]),
        version=int(row["version"]),
        status=str(row["status"]),
        content_hash=str(row["content_hash"]),
        metadata=metadata if isinstance(metadata, dict) else {},
    )


def _user_settings(row: dict[str, Any]) -> UserSettings:
    return UserSettings(
        telegram_user_id=int(row["telegram_user_id"]),
        answer_mode=str(row.get("answer_mode") or "cheap"),
        vision_mode=str(row.get("vision_mode") or "auto"),
        debug_mode=bool(row.get("debug_mode", False)),
        selected_workspace_id=row.get("selected_workspace_id"),
        updated_at=None,
    )
