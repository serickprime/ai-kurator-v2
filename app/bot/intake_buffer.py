"""Telegram multimodal intake buffering."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class UserIntake:
    """One combined user request passed from Telegram into RAG."""

    text: str = ""
    images: tuple[Path, ...] = ()
    files: tuple[Path, ...] = ()
    vision_text: str = ""
    vision_errors: tuple[str, ...] = ()
    telegram_message_ids: tuple[int, ...] = ()
    media_group_id: str | None = None
    conversation_id: str | None = None
    user_settings: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def combined_question(self) -> str:
        """Return text and image context as one question string."""
        parts = []
        if self.text.strip():
            parts.append(self.text.strip())
        if self.vision_text.strip():
            parts.append(f"Контекст изображения: {self.vision_text.strip()}")
        if self.images and not self.vision_text.strip():
            parts.append("Пользователь приложил изображение, но vision context недоступен.")
        if self.files:
            parts.append("Пользователь приложил файл к вопросу.")
        return "\n\n".join(parts).strip()


@dataclass
class IntakeDraft:
    """Mutable short-lived intake draft."""

    text_parts: list[str] = field(default_factory=list)
    images: list[Path] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    vision_parts: list[str] = field(default_factory=list)
    vision_errors: list[str] = field(default_factory=list)
    telegram_message_ids: list[int] = field(default_factory=list)
    media_group_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MessageIntakeBuffer:
    """In-memory buffer for combining text, images, and media groups."""

    def __init__(self) -> None:
        self._drafts: dict[int, IntakeDraft] = {}

    def clear(self, telegram_user_id: int) -> None:
        """Clear pending intake for a user."""
        self._drafts.pop(telegram_user_id, None)

    def add_text(self, telegram_user_id: int, text: str, message_id: int | None = None) -> IntakeDraft:
        """Add text to the user's current intake draft."""
        draft = self._draft(telegram_user_id)
        if text.strip():
            draft.text_parts.append(text.strip())
        if message_id is not None:
            draft.telegram_message_ids.append(message_id)
        return draft

    def add_image(
        self,
        telegram_user_id: int,
        image_path: Path,
        *,
        caption: str = "",
        vision_text: str = "",
        vision_error: str = "",
        message_id: int | None = None,
        media_group_id: str | None = None,
    ) -> IntakeDraft:
        """Add an image and optional caption/vision text to the user's draft."""
        draft = self._draft(telegram_user_id)
        draft.images.append(image_path)
        if caption.strip():
            draft.text_parts.append(caption.strip())
        if vision_text.strip():
            draft.vision_parts.append(vision_text.strip())
        if vision_error.strip():
            draft.vision_errors.append(vision_error.strip())
        if message_id is not None:
            draft.telegram_message_ids.append(message_id)
        draft.media_group_id = media_group_id or draft.media_group_id
        return draft

    def add_file(
        self,
        telegram_user_id: int,
        file_path: Path,
        *,
        caption: str = "",
        message_id: int | None = None,
        media_group_id: str | None = None,
    ) -> IntakeDraft:
        """Add a file to the user's current draft."""
        draft = self._draft(telegram_user_id)
        draft.files.append(file_path)
        if caption.strip():
            draft.text_parts.append(caption.strip())
        if message_id is not None:
            draft.telegram_message_ids.append(message_id)
        draft.media_group_id = media_group_id or draft.media_group_id
        return draft

    def build_intake(
        self,
        telegram_user_id: int,
        *,
        conversation_id: str | None = None,
        user_settings: dict[str, Any] | None = None,
        clear: bool = True,
    ) -> UserIntake:
        """Build a UserIntake from the current draft."""
        draft = self._draft(telegram_user_id)
        intake = UserIntake(
            text="\n".join(draft.text_parts).strip(),
            images=tuple(draft.images),
            files=tuple(draft.files),
            vision_text="\n".join(draft.vision_parts).strip(),
            vision_errors=tuple(draft.vision_errors),
            telegram_message_ids=tuple(dict.fromkeys(draft.telegram_message_ids)),
            media_group_id=draft.media_group_id,
            conversation_id=conversation_id,
            user_settings=user_settings or {},
            created_at=draft.created_at,
        )
        if clear:
            self.clear(telegram_user_id)
        return intake

    def has_pending(self, telegram_user_id: int) -> bool:
        """Return true when the user has pending intake parts."""
        return telegram_user_id in self._drafts

    def _draft(self, telegram_user_id: int) -> IntakeDraft:
        if telegram_user_id not in self._drafts:
            self._drafts[telegram_user_id] = IntakeDraft()
        return self._drafts[telegram_user_id]
