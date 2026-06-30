"""Source-agnostic quality checks for external documentation chunks."""

from __future__ import annotations

import re

MIN_LOW_VALUE_USEFUL_WORDS = 7

FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-я0-9_#+./:-]{2,}", re.UNICODE)
HEADING_LINE_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S+")
URL_RE = re.compile(r"\bhttps?://|localhost:\d+\b", re.IGNORECASE)
ENV_ASSIGNMENT_RE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\s*=\s*\S+")
CLI_COMMAND_RE = re.compile(
    r"(?m)^\s*(?:\$|>)?\s*(?:cd|curl|docker|git|npm|npx|ollama|pip|pnpm|psql|python|supabase|uv|uvicorn|yarn)\b"
)
HTTP_ENDPOINT_RE = re.compile(r"\b(?:DELETE|GET|PATCH|POST|PUT)\s+/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")
API_PATH_RE = re.compile(r"(?<!\w)/(?:api|auth|rest|rpc|v\d+|graphql)\b[^\s`)]*", re.IGNORECASE)
SQL_RE = re.compile(
    r"\b(?:alter\s+table|create\s+(?:extension|function|index|policy|table)|delete\s+from|insert\s+into|select\s+.+\s+from|update\s+\w+)\b",
    re.IGNORECASE | re.DOTALL,
)
JSON_LIKE_RE = re.compile(r"^\s*[\[{].*:[\s\S]*[\]}]\s*$")
FILE_OR_CONFIG_RE = re.compile(
    r"(?:^|[\s`'\"(])(?:[\w.-]+/)*[\w.-]+\.(?:astro|cjs|conf|env|go|ini|js|json|jsx|kt|mjs|py|rs|sql|swift|toml|ts|tsx|vue|yaml|yml)\b",
    re.IGNORECASE,
)
CODE_TOKEN_RE = re.compile(
    r"\b(?:async|await|class|const|def|export|from|function|import|let|return|var)\b.*[;{}()]",
    re.IGNORECASE | re.DOTALL,
)
GENERATED_WIDGET_MARKERS = (
    "ai tools",
    "is this helpful",
    "no project found",
    "no yes",
    "was this helpful",
)


def is_low_value_external_chunk(content: str, *, heading: str = "") -> bool:
    """Return true when a chunk is unlikely to be useful evidence."""
    text = str(content or "").strip()
    if not text:
        return True
    if has_protected_technical_content(text):
        return False
    if _looks_generated_widget(text):
        return True
    if _is_title_only(text, heading):
        return True
    if _mostly_headings(text):
        return True
    return useful_word_count(text) < MIN_LOW_VALUE_USEFUL_WORDS and not has_descriptive_sentence(text)


def has_protected_technical_content(content: str) -> bool:
    """Return true for short chunks that are useful because they are technical examples."""
    text = str(content or "")
    return bool(
        FENCED_CODE_RE.search(text)
        or URL_RE.search(text)
        or ENV_ASSIGNMENT_RE.search(text)
        or CLI_COMMAND_RE.search(text)
        or HTTP_ENDPOINT_RE.search(text)
        or API_PATH_RE.search(text)
        or SQL_RE.search(text)
        or JSON_LIKE_RE.search(text.strip())
        or FILE_OR_CONFIG_RE.search(text)
        or CODE_TOKEN_RE.search(text)
    )


def useful_word_count(content: str) -> int:
    """Count useful text tokens in a deterministic way."""
    return len([token for token in TOKEN_RE.findall(str(content or "")) if len(token.strip("#.")) >= 2])


def has_descriptive_sentence(content: str) -> bool:
    """Return true when text contains at least one normal explanatory sentence."""
    text = _without_fenced_code(str(content or ""))
    for match in re.finditer(r"[^.!?]{20,}[.!?]", text):
        if useful_word_count(match.group(0)) >= 6:
            return True
    return False


def without_fenced_code(content: str) -> str:
    """Remove fenced code blocks before prose-only checks."""
    return _without_fenced_code(content)


def _is_title_only(content: str, heading: str) -> bool:
    normalized_text = _normalize_for_compare(_without_fenced_code(content))
    normalized_heading = _normalize_for_compare(heading)
    if not normalized_text:
        return True
    if not normalized_heading:
        return _mostly_headings(content)
    if normalized_text == normalized_heading:
        return True
    without_heading = normalized_text.replace(normalized_heading, " ").strip()
    return bool(
        not without_heading
        or (useful_word_count(without_heading) < MIN_LOW_VALUE_USEFUL_WORDS and not has_descriptive_sentence(without_heading))
    )


def _mostly_headings(content: str) -> bool:
    lines = [line.strip() for line in _without_fenced_code(content).splitlines() if line.strip()]
    if not lines:
        return True
    heading_lines = [line for line in lines if HEADING_LINE_RE.match(line)]
    if len(heading_lines) == len(lines):
        return True
    remainder = "\n".join(line for line in lines if not HEADING_LINE_RE.match(line))
    return bool(heading_lines and useful_word_count(remainder) < MIN_LOW_VALUE_USEFUL_WORDS)


def _looks_generated_widget(content: str) -> bool:
    normalized = _normalize_for_compare(_without_fenced_code(content))
    if not normalized:
        return True
    return any(marker in normalized for marker in GENERATED_WIDGET_MARKERS) and useful_word_count(normalized) < 8


def _normalize_for_compare(content: str) -> str:
    clean = re.sub(r"^#+\s*", "", str(content or "").strip(), flags=re.MULTILINE)
    clean = re.sub(r"\s+", " ", clean).strip(" -:;,.")
    return clean.casefold()


def _without_fenced_code(content: str) -> str:
    return FENCED_CODE_RE.sub(" ", str(content or ""))
