"""Final answer text cleanup before sources are appended."""

from __future__ import annotations

import re

EMPTY_HEADING_LABELS = {
    "ключевые условия",
    "практический вывод",
    "как работает",
    "важно",
    "итог",
    "вывод",
}

INLINE_SUMMARY_HEADING_LABELS = {
    "ключевые условия",
    "практический вывод",
    "важно",
    "итог",
    "вывод",
}


def clean_answer_format(text: str) -> str:
    """Remove broken list/reference fragments while preserving useful content."""
    raw = str(text or "").strip()
    if not raw:
        return ""

    parts = _split_fenced_code(raw)
    cleaned: list[str] = []
    for kind, value in parts:
        if kind == "code":
            cleaned.append(value)
            continue
        formatted = _clean_text_block(value)
        if formatted:
            cleaned.append(formatted)
    result = "\n\n".join(part.strip("\n") for part in cleaned if part.strip("\n")).strip()
    return result or raw


def _split_fenced_code(text: str) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    position = 0
    for match in re.finditer(r"```(?:[a-zA-Z0-9_+-]+)?\n.*?```", text, flags=re.DOTALL):
        if match.start() > position:
            parts.append(("text", text[position : match.start()]))
        parts.append(("code", match.group(0)))
        position = match.end()
    if position < len(text):
        parts.append(("text", text[position:]))
    return parts


def _clean_text_block(text: str) -> str:
    raw_lines = [line.rstrip() for line in _normalize_text(text).splitlines()]
    lines: list[str] = []
    index = 0
    while index < len(raw_lines):
        line = raw_lines[index].strip()
        if not line:
            lines.append("")
            index += 1
            continue
        if _is_empty_numbered_item(line) or _is_orphan_reference(line):
            index += 1
            continue
        if _is_known_empty_heading(line):
            next_index = _next_meaningful_index(raw_lines, index + 1)
            if next_index is None or _is_known_empty_heading(raw_lines[next_index].strip()):
                index += 1
                continue
            next_line = raw_lines[next_index].strip()
            if _should_inline_heading(line) and not _looks_like_list_item(next_line) and not _is_orphan_reference(next_line):
                lines.append(_inline_heading(line, next_line))
                index = next_index + 1
                continue
        lines.append(line)
        index += 1
    return _collapse_blank_lines("\n".join(lines))


def _normalize_text(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u00a0", " ").replace("\u202f", " ")
    normalized = re.sub(r"[ \t]{2,}", " ", normalized)
    normalized = re.sub(r"\(\s*см\.\s*\n\s*раздел\s+([^)]+)\)\.?", r"", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\(\s*see\s*\n\s*section\s+([^)]+)\)\.?", r"", normalized, flags=re.IGNORECASE)
    return normalized


def _is_empty_numbered_item(line: str) -> bool:
    return bool(re.fullmatch(r"(?:[-*]\s*)?\d+[\.)]\s*", line.strip()))


def _is_orphan_reference(line: str) -> bool:
    clean = line.strip().strip("()").strip()
    clean = re.sub(r"\s+", " ", clean)
    lowered = clean.casefold().strip(". ")
    if lowered in {"см", "см.", "see", "see.", "section", "раздел"}:
        return True
    if re.fullmatch(r"(?:см\.?|see)\s*(?:раздел|section)?", lowered):
        return True
    if re.fullmatch(r"(?:раздел|section)\s+[\w ._/\-]{1,80}", lowered, flags=re.IGNORECASE):
        return True
    return False


def _is_known_empty_heading(line: str) -> bool:
    return _heading_label(line) in EMPTY_HEADING_LABELS


def _should_inline_heading(line: str) -> bool:
    clean = line.strip()
    return clean.endswith(":") or _heading_label(clean) in INLINE_SUMMARY_HEADING_LABELS


def _inline_heading(line: str, next_line: str) -> str:
    clean = line.strip()
    separator = " " if clean.endswith(":") else ": "
    return f"{clean}{separator}{next_line.strip()}"


def _heading_label(line: str) -> str:
    clean = re.sub(r"\s+", " ", line.strip()).strip(":").strip()
    return clean.casefold()


def _next_meaningful_index(lines: list[str], start: int) -> int | None:
    for index in range(start, len(lines)):
        line = lines[index].strip()
        if not line:
            continue
        if _is_empty_numbered_item(line) or _is_orphan_reference(line):
            continue
        return index
    return None


def _looks_like_list_item(line: str) -> bool:
    return bool(re.match(r"^(?:[-*]\s+|\d+[\.)]\s+\S)", line.strip()))


def _collapse_blank_lines(text: str) -> str:
    lines: list[str] = []
    previous_blank = False
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        blank = not line.strip()
        if blank and previous_blank:
            continue
        lines.append("" if blank else line)
        previous_blank = blank
    return "\n".join(lines).strip()
