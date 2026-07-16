"""Repository layer for Supabase access."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.ingestion.chunker import ChunkDraft, SectionDraft
from app.ingestion.document_cards import DocumentCard
from app.db.supabase_client import SupabaseRequestError

if TYPE_CHECKING:
    from app.db.supabase_client import SupabaseClient

LOGGER = logging.getLogger(__name__)

_DOCS_CANDIDATE_SUGGESTION_STATUSES = frozenset(
    {"pending", "preview_ready", "approved", "rejected", "failed", "activated"}
)
_DOCS_CANDIDATE_PREVIEW_STATUSES = frozenset({"not_run", "ok", "failed", "needs_review"})
_DOCS_CANDIDATE_REVIEWED_STATUSES = frozenset({"approved", "rejected", "activated"})
_SERVICE_ID_DEDUPE_RE = re.compile(r"[^a-z0-9]+")


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


@dataclass(frozen=True)
class DocsCandidateSuggestion:
    """Persistent owner-review record for a documentation candidate."""

    id: str
    workspace_id: str
    service_id: str
    display_name: str
    aliases: tuple[str, ...]
    official_url: str
    allowed_domain: str
    source_query: str = ""
    discovery_reason: str = ""
    confidence: float = 0.0
    risk_level: str = "review"
    status: str = "pending"
    preview_status: str = "not_run"
    preview_result: dict[str, Any] = field(default_factory=dict)
    requested_by_user_id: int | None = None
    created_at: str = ""
    updated_at: str = ""
    reviewed_at: str | None = None
    reviewed_by_user_id: int | None = None
    rejection_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


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

    async def archive_external_document_exact(
        self,
        *,
        document_id: str,
        workspace_id: str,
        document_key: str,
        source_id: str,
        expected_version: int,
    ) -> int:
        """Archive exactly one active external-doc document row."""
        updated = await self._client.update(
            "documents",
            {"status": "archived"},
            params={
                "id": f"eq.{document_id}",
                "workspace_id": f"eq.{workspace_id}",
                "document_key": f"eq.{document_key}",
                "source_type": "eq.external_docs",
                "metadata->>source_name": f"eq.{source_id}",
                "status": "eq.active",
                "version": f"eq.{expected_version}",
            },
        )
        return len(updated)

    async def delete_incomplete_external_document_draft_exact(
        self,
        *,
        document_id: str,
        workspace_id: str,
        document_key: str,
        source_id: str,
        expected_version: int,
        expected_content_hash: str,
        expected_ingestion_signature: str,
    ) -> int:
        """Delete exactly one verified draft external-doc document row."""
        params = {
            "id": f"eq.{document_id}",
            "workspace_id": f"eq.{workspace_id}",
            "document_key": f"eq.{document_key}",
            "source_type": "eq.external_docs",
            "metadata->>source_name": f"eq.{source_id}",
            "status": "eq.draft",
            "version": f"eq.{expected_version}",
            "content_hash": f"eq.{expected_content_hash}",
        }
        if expected_ingestion_signature:
            params["metadata->ingestion->>signature"] = f"eq.{expected_ingestion_signature}"
        deleted = await self._client.delete("documents", params=params)
        return len(deleted)

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


class DocsCandidateSuggestionRepository:
    """Database access for persistent docs candidate suggestions."""

    _SELECT = (
        "id,workspace_id,service_id,display_name,aliases,official_url,allowed_domain,"
        "source_query,discovery_reason,confidence,risk_level,status,preview_status,"
        "preview_result,requested_by_user_id,created_at,updated_at,reviewed_at,"
        "reviewed_by_user_id,rejection_reason,metadata"
    )

    def __init__(self, client: "SupabaseClient") -> None:
        self._client = client

    async def get(self, suggestion_id: str) -> DocsCandidateSuggestion | None:
        """Return one suggestion by id."""
        rows = await self._client.select(
            "docs_candidate_suggestions",
            params={"select": self._SELECT, "id": f"eq.{suggestion_id}", "limit": "1"},
        )
        return _docs_candidate_suggestion(rows[0]) if rows else None

    async def list_pending(
        self,
        workspace_id: str,
        *,
        limit: int = 10,
    ) -> tuple[DocsCandidateSuggestion, ...]:
        """Return pending, preview-ready, or failed suggestions for owner review."""
        if limit <= 0:
            return ()
        rows = await self._client.select(
            "docs_candidate_suggestions",
            params={
                "select": self._SELECT,
                "workspace_id": f"eq.{workspace_id}",
                "status": "in.(pending,preview_ready,failed)",
                "order": "updated_at.desc",
                "limit": str(limit),
            },
        )
        return tuple(_docs_candidate_suggestion(row) for row in rows)

    async def find_by_service_url(
        self,
        *,
        workspace_id: str,
        service_id: str,
        official_url: str,
    ) -> DocsCandidateSuggestion | None:
        """Return an existing suggestion for service/url after normalized comparison."""
        normalized_service_id = _normalize_service_id_for_dedupe(service_id)
        normalized_url = _normalize_url_for_dedupe(official_url)
        if not normalized_service_id or not normalized_url:
            return None
        rows = await self._client.select(
            "docs_candidate_suggestions",
            params={
                "select": self._SELECT,
                "workspace_id": f"eq.{workspace_id}",
                "order": "updated_at.desc",
                "limit": "200",
            },
        )
        for row in rows:
            if (
                _normalize_service_id_for_dedupe(str(row.get("service_id") or "")) == normalized_service_id
                and _normalize_url_for_dedupe(str(row.get("official_url") or "")) == normalized_url
            ):
                return _docs_candidate_suggestion(row)
        return None

    async def recent_for_service(
        self,
        *,
        workspace_id: str,
        service_id: str,
        limit: int = 10,
    ) -> tuple[DocsCandidateSuggestion, ...]:
        """Return recent suggestions for a service, including rejected ones for cooldown."""
        rows = await self._client.select(
            "docs_candidate_suggestions",
            params={
                "select": self._SELECT,
                "workspace_id": f"eq.{workspace_id}",
                "service_id": f"eq.{service_id}",
                "order": "updated_at.desc",
                "limit": str(limit),
            },
        )
        return tuple(_docs_candidate_suggestion(row) for row in rows)

    async def create_pending(
        self,
        *,
        workspace_id: str,
        service_id: str,
        display_name: str,
        aliases: tuple[str, ...],
        official_url: str,
        allowed_domain: str,
        source_query: str,
        discovery_reason: str,
        confidence: float,
        risk_level: str,
        requested_by_user_id: int | None,
        metadata: dict[str, Any] | None = None,
    ) -> DocsCandidateSuggestion:
        """Create one sanitized pending suggestion."""
        clean_workspace_id = _required_text(workspace_id, "workspace_id")
        clean_service_id = _required_text(service_id, "service_id")
        clean_official_url = _required_text(official_url, "official_url")
        existing = await self.find_by_service_url(
            workspace_id=clean_workspace_id,
            service_id=clean_service_id,
            official_url=clean_official_url,
        )
        if existing is not None:
            return existing
        payload = {
            "workspace_id": clean_workspace_id,
            "service_id": clean_service_id,
            "display_name": _required_text(display_name, "display_name"),
            "aliases": list(_clean_text_tuple(aliases)),
            "official_url": clean_official_url,
            "allowed_domain": _required_text(allowed_domain, "allowed_domain").casefold(),
            "source_query": str(source_query or "").strip(),
            "discovery_reason": str(discovery_reason or "").strip(),
            "confidence": max(0.0, min(float(confidence), 1.0)),
            "risk_level": _validate_risk_level(risk_level),
            "status": "pending",
            "preview_status": "not_run",
            "preview_result": {},
            "requested_by_user_id": requested_by_user_id,
            "metadata": metadata or {},
        }
        try:
            rows = await self._client.insert("docs_candidate_suggestions", payload)
        except SupabaseRequestError as exc:
            if not _is_duplicate_suggestion_error(exc):
                raise
            existing = await self.find_by_service_url(
                workspace_id=clean_workspace_id,
                service_id=clean_service_id,
                official_url=clean_official_url,
            )
            if existing is not None:
                return existing
            raise
        return _docs_candidate_suggestion(_first_row(rows, "create docs candidate suggestion"))

    async def create(
        self,
        *,
        workspace_id: str,
        service_id: str,
        display_name: str,
        aliases: tuple[str, ...],
        official_url: str,
        allowed_domain: str,
        source_query: str,
        discovery_reason: str,
        confidence: float,
        risk_level: str,
        requested_by_user_id: int | None,
        metadata: dict[str, Any] | None = None,
    ) -> DocsCandidateSuggestion:
        """Backward-compatible alias for creating a pending suggestion."""
        return await self.create_pending(
            workspace_id=workspace_id,
            service_id=service_id,
            display_name=display_name,
            aliases=aliases,
            official_url=official_url,
            allowed_domain=allowed_domain,
            source_query=source_query,
            discovery_reason=discovery_reason,
            confidence=confidence,
            risk_level=risk_level,
            requested_by_user_id=requested_by_user_id,
            metadata=metadata,
        )

    async def save_preview_result(
        self,
        suggestion_id: str,
        *,
        preview_status: str,
        preview_result: dict[str, Any],
        status: str | None = None,
    ) -> DocsCandidateSuggestion:
        """Store sanitized preview result for a candidate."""
        clean_preview_status = _validate_preview_status(preview_status)
        clean_status = _validate_suggestion_status(status or _status_for_preview(clean_preview_status))
        rows = await self._client.update(
            "docs_candidate_suggestions",
            {
                "status": clean_status,
                "preview_status": clean_preview_status,
                "preview_result": preview_result,
            },
            params={"id": f"eq.{suggestion_id}"},
        )
        return _docs_candidate_suggestion(_first_row(rows, "save docs candidate preview result"))

    async def update_preview(
        self,
        suggestion_id: str,
        *,
        status: str,
        preview_status: str,
        preview_result: dict[str, Any],
    ) -> DocsCandidateSuggestion:
        """Backward-compatible alias for storing a preview result."""
        return await self.save_preview_result(
            suggestion_id,
            status=status,
            preview_status=preview_status,
            preview_result=preview_result,
        )

    async def update_status(
        self,
        suggestion_id: str,
        status: str,
        *,
        reviewed_by_user_id: int | None = None,
        rejection_reason: str = "",
    ) -> DocsCandidateSuggestion:
        """Change suggestion status without deleting review history."""
        clean_status = _validate_suggestion_status(status)
        payload: dict[str, Any] = {"status": clean_status}
        if reviewed_by_user_id is not None:
            payload["reviewed_by_user_id"] = reviewed_by_user_id
        if clean_status in _DOCS_CANDIDATE_REVIEWED_STATUSES:
            payload["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        if clean_status == "rejected":
            payload["rejection_reason"] = rejection_reason or "owner_rejected"

        rows = await self._client.update(
            "docs_candidate_suggestions",
            payload,
            params={"id": f"eq.{suggestion_id}"},
        )
        return _docs_candidate_suggestion(_first_row(rows, "update docs candidate suggestion status"))

    async def mark_approved(self, suggestion_id: str, *, reviewed_by_user_id: int) -> DocsCandidateSuggestion:
        """Mark a preview-ready candidate approved before activation."""
        return await self.update_status(suggestion_id, "approved", reviewed_by_user_id=reviewed_by_user_id)

    async def mark_activated(self, suggestion_id: str, *, reviewed_by_user_id: int) -> DocsCandidateSuggestion:
        """Mark a candidate activated after controlled indexing succeeds."""
        return await self.update_status(suggestion_id, "activated", reviewed_by_user_id=reviewed_by_user_id)

    async def save_activation_result(
        self,
        suggestion_id: str,
        *,
        activation_result: dict[str, Any],
        status: str,
        reviewed_by_user_id: int | None = None,
    ) -> DocsCandidateSuggestion:
        """Store a compact activation result in metadata and update review status."""
        clean_status = _validate_suggestion_status(status)
        current = await self.get(suggestion_id)
        metadata = dict(current.metadata) if current is not None else {}
        metadata["activation_result"] = activation_result
        payload: dict[str, Any] = {
            "status": clean_status,
            "metadata": metadata,
        }
        if reviewed_by_user_id is not None:
            payload["reviewed_by_user_id"] = reviewed_by_user_id
        if clean_status in _DOCS_CANDIDATE_REVIEWED_STATUSES or reviewed_by_user_id is not None:
            payload["reviewed_at"] = datetime.now(timezone.utc).isoformat()

        rows = await self._client.update(
            "docs_candidate_suggestions",
            payload,
            params={"id": f"eq.{suggestion_id}"},
        )
        return _docs_candidate_suggestion(_first_row(rows, "save docs candidate activation result"))

    async def reject(
        self,
        suggestion_id: str,
        *,
        reviewed_by_user_id: int,
        rejection_reason: str = "",
    ) -> DocsCandidateSuggestion:
        """Reject a candidate without deleting it, preserving dedupe/cooldown."""
        return await self.update_status(
            suggestion_id,
            "rejected",
            reviewed_by_user_id=reviewed_by_user_id,
            rejection_reason=rejection_reason,
        )

    async def mark_rejected(
        self,
        suggestion_id: str,
        *,
        reviewed_by_user_id: int,
        rejection_reason: str = "",
    ) -> DocsCandidateSuggestion:
        """Backward-compatible alias for rejecting a candidate."""
        return await self.reject(
            suggestion_id,
            reviewed_by_user_id=reviewed_by_user_id,
            rejection_reason=rejection_reason,
        )


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


def _docs_candidate_suggestion(row: dict[str, Any]) -> DocsCandidateSuggestion:
    preview_result = row.get("preview_result")
    metadata = row.get("metadata")
    return DocsCandidateSuggestion(
        id=str(row.get("id") or ""),
        workspace_id=str(row.get("workspace_id") or ""),
        service_id=str(row.get("service_id") or ""),
        display_name=str(row.get("display_name") or ""),
        aliases=tuple(str(item) for item in row.get("aliases") or () if str(item).strip()),
        official_url=str(row.get("official_url") or ""),
        allowed_domain=str(row.get("allowed_domain") or ""),
        source_query=str(row.get("source_query") or ""),
        discovery_reason=str(row.get("discovery_reason") or ""),
        confidence=float(row.get("confidence") or 0.0),
        risk_level=str(row.get("risk_level") or "review"),
        status=str(row.get("status") or "pending"),
        preview_status=str(row.get("preview_status") or "not_run"),
        preview_result=preview_result if isinstance(preview_result, dict) else {},
        requested_by_user_id=_int_or_none(row.get("requested_by_user_id")),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
        reviewed_at=str(row.get("reviewed_at") or "") or None,
        reviewed_by_user_id=_int_or_none(row.get("reviewed_by_user_id")),
        rejection_reason=str(row.get("rejection_reason") or ""),
        metadata=metadata if isinstance(metadata, dict) else {},
    )


def _normalize_url_for_dedupe(value: str) -> str:
    return str(value or "").strip().rstrip("/").casefold()


def _normalize_service_id_for_dedupe(value: str) -> str:
    return _SERVICE_ID_DEDUPE_RE.sub("_", str(value or "").strip().casefold())


def _required_text(value: str, field_name: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{field_name} is required")
    return clean


def _clean_text_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _validate_suggestion_status(value: str) -> str:
    clean = str(value or "").strip()
    if clean not in _DOCS_CANDIDATE_SUGGESTION_STATUSES:
        raise ValueError(f"Unsupported docs candidate suggestion status: {value!r}")
    return clean


def _validate_preview_status(value: str) -> str:
    clean = str(value or "").strip()
    if clean not in _DOCS_CANDIDATE_PREVIEW_STATUSES:
        raise ValueError(f"Unsupported docs candidate preview status: {value!r}")
    return clean


def _validate_risk_level(value: str) -> str:
    clean = str(value or "").strip()
    if clean not in {"low", "medium", "review"}:
        raise ValueError(f"Unsupported docs candidate risk level: {value!r}")
    return clean


def _status_for_preview(preview_status: str) -> str:
    if preview_status == "failed":
        return "failed"
    if preview_status == "not_run":
        return "pending"
    return "preview_ready"


def _first_row(rows: list[dict[str, Any]], action: str) -> dict[str, Any]:
    if not rows:
        raise LookupError(f"No row returned while trying to {action}.")
    return rows[0]


def _is_duplicate_suggestion_error(exc: SupabaseRequestError) -> bool:
    body = exc.body.casefold()
    return (
        exc.status_code == 409
        or "23505" in body
        or "duplicate" in body
        or "docs_candidate_suggestions_workspace_service_url_key" in body
    )


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
