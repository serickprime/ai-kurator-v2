"""Classify short Telegram notes before they become RAG questions."""

from __future__ import annotations

TOPIC_HINT_ALIASES = (
    "n8n",
    "н8н",
    "supabase",
    "docker",
    "flutterflow",
    "yoomoney",
    "yookassa",
    "openrouter",
)


def classify_intake_note_text(text: str) -> str:
    """Classify a short note as topic/style/source/task instruction or empty."""
    raw = text or ""
    normalized = " ".join(raw.strip().lower().split())
    if not normalized or normalized.startswith("/"):
        return ""
    if "\n" in raw or "http://" in normalized or "https://" in normalized:
        return ""
    words = normalized.split()
    if len(words) > 10 or len(normalized) > 140:
        return ""

    if normalized in TOPIC_HINT_ALIASES:
        return "topic_hint"
    if any(alias in normalized for alias in TOPIC_HINT_ALIASES) and len(words) <= 5 and "?" not in normalized:
        return "topic_hint"

    style_terms = (
        "коротко",
        "кратко",
        "простыми",
        "понятно",
        "подробно",
        "без списка",
        "списком",
        "делово",
        "дружелюбно",
    )
    task_terms = (
        "ответь",
        "напиши",
        "сформулируй",
        "объясни",
        "поясни",
        "разбери",
        "проверь",
        "найди",
        "скажи",
        "помоги",
        "исправь",
    )
    source_terms = (
        "по материал",
        "по урок",
        "по базе",
        "по документац",
        "официальн",
        "docs",
    )
    context_refs = (
        "этот",
        "эта",
        "это",
        "этом",
        "тут",
        "здесь",
        "скрин",
        "сообщени",
        "вопрос",
        "студент",
        "изображ",
        "картин",
        "фото",
    )
    visual_refs = ("изображ", "картин", "фото", "скрин", "видно")

    if any(term in normalized for term in source_terms):
        return "source_instruction"
    if any(term in normalized for term in visual_refs) and (
        "?" in normalized
        or any(term in normalized for term in task_terms)
        or normalized.startswith(("что ", "где ", "почему "))
    ):
        return "task_instruction"
    if any(term in normalized for term in style_terms) and any(term in normalized for term in task_terms):
        return "answer_style"
    if any(term in normalized for term in style_terms) and len(words) <= 6:
        return "answer_style"
    if any(term in normalized for term in task_terms) and any(term in normalized for term in context_refs):
        return "task_instruction"
    if "?" in normalized and any(term in normalized for term in context_refs):
        return "context_dependent_instruction"
    return ""


def should_store_intake_note(note_type: str, has_recent_dialog_messages: bool = False) -> bool:
    """Return true when a classified note is safe to keep for the next intake."""
    if not note_type:
        return False
    if note_type == "context_dependent_instruction" and has_recent_dialog_messages:
        return False
    return True
