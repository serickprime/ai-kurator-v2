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

EVIDENCE_LABEL_PATTERN = r"(?:accepted\s+evidence|supporting\s+evidence|evidence\s+quote|evidence)"
EVIDENCE_QUOTED_FRAGMENT_RE = re.compile(
    rf"\s*(?:\(\s*)?{EVIDENCE_LABEL_PATTERN}\s*:\s*(?:[\"'“«].*?[\"'”»])\s*\)?",
    flags=re.IGNORECASE,
)
EVIDENCE_TRAILING_FRAGMENT_RE = re.compile(rf"\s*(?:\(\s*)?{EVIDENCE_LABEL_PATTERN}\s*:\s+.*$", flags=re.IGNORECASE)


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
    raw_lines = _rewrite_wide_markdown_tables(raw_lines)
    lines: list[str] = []
    index = 0
    while index < len(raw_lines):
        line = raw_lines[index].strip()
        if not line:
            lines.append("")
            index += 1
            continue
        if _is_evidence_artifact_line(line):
            index += 1
            continue
        line = _strip_inline_evidence_artifacts(line)
        if not line:
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


def _is_evidence_artifact_line(line: str) -> bool:
    clean = re.sub(r"^(?:[-*]\s+|\d+[\.)]\s+)", "", line.strip())
    return bool(re.match(rf"^{EVIDENCE_LABEL_PATTERN}\s*:", clean, flags=re.IGNORECASE))


def _strip_inline_evidence_artifacts(line: str) -> str:
    cleaned = EVIDENCE_QUOTED_FRAGMENT_RE.sub("", line)
    cleaned = EVIDENCE_TRAILING_FRAGMENT_RE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)
    cleaned = cleaned.strip()
    if cleaned in {"-", "*"}:
        return ""
    return cleaned


def _rewrite_wide_markdown_tables(lines: list[str]) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(lines):
        if _starts_markdown_table(lines, index):
            end = index + 2
            while end < len(lines) and _is_table_line(lines[end]):
                end += 1
            block = lines[index:end]
            converted = _convert_wide_markdown_table(block)
            if converted is not None:
                result.extend(converted)
                index = end
                continue
        result.append(lines[index])
        index += 1
    return result


def _starts_markdown_table(lines: list[str], index: int) -> bool:
    return index + 2 < len(lines) and _is_table_line(lines[index]) and _is_separator_row(lines[index + 1])


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and stripped.count("|") >= 2


def _is_separator_row(line: str) -> bool:
    cells = _split_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _convert_wide_markdown_table(block: list[str]) -> list[str] | None:
    headers = _split_table_row(block[0])
    rows = [_split_table_row(line) for line in block[2:] if _is_table_line(line) and not _is_separator_row(line)]
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not headers or not rows or not _should_convert_table(block, headers, rows):
        return None

    return [_format_table_row_as_list_item(headers, row) for row in rows]


def _should_convert_table(block: list[str], headers: list[str], rows: list[list[str]]) -> bool:
    max_line_length = max((len(line.strip()) for line in block), default=0)
    max_cell_length = max((len(cell.strip()) for row in rows for cell in row), default=0)
    return max_line_length > 88 or len(headers) >= 4 or max_cell_length > 42


def _format_table_row_as_list_item(headers: list[str], row: list[str]) -> str:
    cells = row + [""] * max(0, len(headers) - len(row))
    label = (cells[0] or headers[0] or "Item").strip()
    details: list[str] = []
    for index, header in enumerate(headers[1:], start=1):
        if index >= len(cells):
            break
        value = cells[index].strip()
        if value:
            details.append(f"{header.strip() or f'Column {index + 1}'}: {value}")
    if not details:
        return f"- {label}"
    return f"- {label} - {'; '.join(details)}"


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
