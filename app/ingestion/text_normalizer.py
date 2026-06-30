"""Text cleanup helpers used before chunking and indexing."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable

PAGE_MARKER_RE = re.compile(r"^\[\[page:\d+]]\s*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
CYRILLIC_TOKEN_RE = re.compile(r"[А-Яа-яЁё]{16,}")
GENERIC_HEADING_RE = re.compile(
    r"^(?:heading|header|section|page|заголовок|раздел|страница)\s*[\dIVXLCDM]+$",
    re.IGNORECASE,
)

BOILERPLATE_LABELS = {
    "",
    "unknown",
    "none",
    "null",
    "document",
    "прочее",
    "название файла",
    "название файла:",
    "source file",
    "source file:",
}

_RUSSIAN_WORDS = {
    "а",
    "без",
    "будет",
    "важно",
    "внимательно",
    "все",
    "всё",
    "выбери",
    "выполни",
    "где",
    "для",
    "добавь",
    "если",
    "запрос",
    "запросы",
    "затем",
    "и",
    "из",
    "или",
    "инструкцию",
    "как",
    "команду",
    "контекст",
    "мне",
    "можно",
    "на",
    "настраивать",
    "напишу",
    "настрой",
    "настроить",
    "нужно",
    "объясни",
    "открой",
    "потом",
    "почему",
    "правила",
    "проверь",
    "проект",
    "прочитай",
    "работает",
    "раздел",
    "с",
    "сделай",
    "составлять",
    "создай",
    "сохрани",
    "так",
    "файл",
    "что",
    "чтобы",
    "это",
    "я",
}


class TextNormalizer:
    """Normalize extracted text while preserving code-like content."""

    def normalize(self, text: str) -> str:
        """Return cleaned text suitable for sectioning and chunking."""
        text = _normalize_newlines(text)
        blocks = _split_fenced_code(text)
        normalized: list[str] = []
        for block in blocks:
            if block.startswith("```"):
                normalized.append(block.strip())
            else:
                normalized.append(self._normalize_prose_block(block))
        return _normalize_blank_lines("\n\n".join(part for part in normalized if part.strip()))

    def _normalize_prose_block(self, text: str) -> str:
        lines = [_normalize_spaces(line).strip() for line in text.splitlines()]
        lines = [line for line in lines if not is_boilerplate_line(line)]
        lines = _join_wrapped_lines(lines)
        return "\n".join(_repair_glued_cyrillic(line) for line in lines).strip()


def normalize_text(text: str) -> str:
    """Convenience wrapper for one-off normalization."""
    return TextNormalizer().normalize(text)


def clean_heading(value: object, *, fallback: str = "") -> str:
    """Return a user-facing heading/title or fallback when the value is boilerplate."""
    text = _normalize_spaces(str(value or "")).strip()
    text = re.sub(r"^#{1,6}\s+", "", text).strip()
    text = _strip_file_prefix(text)
    text = text.strip(" -–—,:;")
    if not text or is_boilerplate_label(text) or is_generic_heading(text):
        return fallback
    if _looks_like_page_marker(text):
        return fallback
    if _looks_like_source_line(text):
        return fallback
    if len(text) > 160:
        text = text[:157].rstrip() + "..."
    return text


def title_from_text_or_filename(text: str, path: Path) -> str:
    """Pick a meaningful document title from headings, otherwise use the filename stem."""
    filename_title = path.stem.replace("_", " ").replace("-", " ").strip() or path.name
    for line in text.splitlines():
        match = HEADING_RE.match(line.strip())
        if not match:
            continue
        title = clean_heading(match.group(2), fallback="")
        if title:
            return title
    return filename_title


def is_boilerplate_line(line: str) -> bool:
    """Return true for extraction scaffolding that should not enter evidence chunks."""
    clean = _normalize_spaces(line).strip()
    if not clean:
        return False
    if PAGE_MARKER_RE.match(clean):
        return False
    if _looks_like_source_line(clean):
        return True
    return is_boilerplate_label(clean)


def is_boilerplate_label(value: object) -> bool:
    """Return true when a title/heading/label is not meaningful."""
    clean = _normalize_spaces(str(value or "")).strip(" -–—,:;").casefold()
    if clean in BOILERPLATE_LABELS:
        return True
    if clean.startswith("название файла"):
        return True
    if clean.startswith("source file:"):
        return True
    return False


def is_generic_heading(value: object) -> bool:
    """Return true for placeholder headings that are not useful as source labels."""
    clean = _normalize_spaces(str(value or "")).strip(" -–—,:;").casefold()
    if not clean:
        return False
    if is_boilerplate_label(clean):
        return True
    return bool(GENERIC_HEADING_RE.fullmatch(clean))


def has_suspicious_glued_cyrillic_text(text: str) -> bool:
    """Return true when text contains long Cyrillic-only tokens that look glued."""
    for token in CYRILLIC_TOKEN_RE.findall(text):
        repaired = _split_glued_cyrillic_token(token)
        if repaired and repaired.casefold() != token.casefold():
            return True
    return False


def join_pdf_spans(spans: Iterable[dict[str, object]]) -> str:
    """Join PyMuPDF spans without gluing adjacent words."""
    result = ""
    previous_bbox: tuple[float, float, float, float] | None = None
    for span in spans:
        text = str(span.get("text") or "")
        if not text:
            continue
        bbox = _bbox(span.get("bbox"))
        if result and _needs_span_space(result, text, previous_bbox, bbox):
            result += " "
        result += text
        previous_bbox = bbox
    return _normalize_spaces(result).strip()


def _normalize_newlines(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")


def _normalize_spaces(text: str) -> str:
    return re.sub(r"[ \t\f\v]+", " ", text)


def _normalize_blank_lines(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _split_fenced_code(text: str) -> list[str]:
    return re.split(r"(```.*?```)", text, flags=re.DOTALL)


def _join_wrapped_lines(lines: list[str]) -> list[str]:
    result: list[str] = []
    current = ""
    for line in lines:
        if not line:
            if current:
                result.append(current)
                current = ""
            result.append("")
            continue
        if _line_must_stay_separate(line):
            if current:
                result.append(current)
                current = ""
            result.append(line)
            continue
        if current and _can_join_wrapped_line(current, line):
            current = f"{current} {line}".strip()
        else:
            if current:
                result.append(current)
            current = line
    if current:
        result.append(current)
    return result


def _line_must_stay_separate(line: str) -> bool:
    stripped = line.strip()
    if PAGE_MARKER_RE.match(stripped) or HEADING_RE.match(stripped):
        return True
    if re.match(r"^([-*•]|\d+[.)])\s+\S+", stripped):
        return True
    if re.match(r"^\|.*\|$", stripped):
        return True
    if _looks_like_code_like_line(stripped):
        return True
    return False


def _can_join_wrapped_line(previous: str, line: str) -> bool:
    if previous.endswith((".", "!", "?", ":", ";", "```")):
        return False
    if _line_must_stay_separate(previous) or _line_must_stay_separate(line):
        return False
    return True


def _looks_like_code_like_line(line: str) -> bool:
    if not line:
        return False
    if re.search(r"https?://|[A-Za-z]:\\|/[A-Za-z0-9_.-]+/", line):
        return True
    if re.search(r"\b(?:curl|docker|npm|npx|python|pip|git|psql|supabase)\b", line):
        return True
    if re.search(r"[{}[\];]|^\s*[A-Za-z_][A-Za-z0-9_]*\s*[:=]", line):
        return True
    return False


def _repair_glued_cyrillic(line: str) -> str:
    if _looks_like_code_like_line(line):
        return line

    def repl(match: re.Match[str]) -> str:
        token = match.group(0)
        return _split_glued_cyrillic_token(token) or token

    return CYRILLIC_TOKEN_RE.sub(repl, line)


def _split_glued_cyrillic_token(token: str) -> str:
    lowered = token.casefold().replace("ё", "е")
    words = {word.replace("ё", "е") for word in _RUSSIAN_WORDS}
    best: list[str] | None = None

    def walk(position: int, parts: list[str]) -> None:
        nonlocal best
        if best is not None and len(parts) >= len(best):
            return
        if position >= len(lowered):
            best = list(parts)
            return
        for end in range(len(lowered), position + 1, -1):
            candidate = lowered[position:end]
            if candidate in words:
                walk(end, [*parts, token[position:end]])

    walk(0, [])
    if best is None or len(best) < 2:
        return ""
    covered = sum(len(part) for part in best)
    if covered / max(len(token), 1) < 0.9:
        return ""
    result = " ".join(best)
    if token[:1].isupper():
        result = result[:1].upper() + result[1:]
    return result


def _needs_span_space(
    current: str,
    next_text: str,
    previous_bbox: tuple[float, float, float, float] | None,
    next_bbox: tuple[float, float, float, float] | None,
) -> bool:
    if current.endswith((" ", "\n", "/", "\\", "-", "_", ".", ":", "#")):
        return False
    if next_text.startswith((" ", "\n", ".", ",", ":", ";", ")", "]", "}", "/", "\\", "-")):
        return False
    if not (current[-1:].isalnum() and next_text[:1].isalnum()):
        return False
    if previous_bbox is not None and next_bbox is not None:
        gap = next_bbox[0] - previous_bbox[2]
        return gap > 1.0
    return True


def _bbox(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return tuple(float(item) for item in value)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def _looks_like_source_line(text: str) -> bool:
    return text.casefold().startswith("source file:")


def _strip_file_prefix(text: str) -> str:
    if text.casefold().startswith("source file:"):
        return text.split(":", 1)[1].strip()
    return text


def _looks_like_page_marker(text: str) -> bool:
    return bool(PAGE_MARKER_RE.match(text) or re.fullmatch(r"(?:page|страница|стр\.?)\s*\d+", text, re.IGNORECASE))
