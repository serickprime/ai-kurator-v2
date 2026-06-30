"""Quality validation for indexed external documentation."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Literal

from app.external_docs.chunk_quality import has_protected_technical_content, without_fenced_code
from app.rag.source_labels import SourceLabelBuilder
from app.rag.types import SourceRef

QualityStatus = Literal["PASS", "WARN", "FAIL"]

RAW_HTML_RE = re.compile(
    r"</?(?:html|body|div|span|script|style|nav|footer|header|aside|main|section|article|button|ul|ol|li|a)\b"
    r"|class=|data-[\w-]+=",
    re.IGNORECASE,
)
PREVIOUS_NEXT_RE = re.compile(r"\bprevious\b.+\bnext\b", re.IGNORECASE)
TOKEN_RE = re.compile(r"[A-Za-z0-9_#+./:-]{2,}", re.IGNORECASE)

NAV_FOOTER_MARKERS = (
    "skip to content",
    "on this page",
    "table of contents",
    "was this helpful",
    "edit this page",
    "last updated",
    "cookie banner",
    "cookie settings",
    "accept all cookies",
    "navigation menu",
    "sidebar",
)
GENERATOR_MARKERS = (
    "for the complete documentation index",
    "this page is also available as markdown",
    "llms.txt",
)
TECHNICAL_SOURCE_MARKERS = (
    "api",
    "cli",
    "code",
    "developer",
    "install",
    "integration",
    "node",
    "sdk",
    "workflow",
)

TITLE_ONLY_WARN_RATIO = 0.15
VERY_SHORT_WARN_RATIO = 0.25
WITHOUT_USEFUL_TEXT_WARN_RATIO = 0.15
ARCHIVED_ACTIVE_WARN_RATIO = 3.0
VERY_SHORT_USEFUL_WORDS = 8
MIN_USEFUL_WORDS = 5
HUGE_CHUNK_CHARS = 12000


@dataclass(frozen=True)
class ExternalDocsValidationResult:
    """Validation report for one external docs source."""

    source_name: str
    quality: QualityStatus
    metrics: dict[str, int | float | bool]
    failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    samples: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe report."""
        return {
            "source_name": self.source_name,
            "quality": self.quality,
            "metrics": self.metrics,
            "failures": list(self.failures),
            "warnings": list(self.warnings),
            "samples": self.samples,
        }


def validate_external_docs(
    *,
    source_name: str,
    documents: Iterable[dict[str, Any]],
    chunks: Iterable[dict[str, Any]],
    sample_size: int = 10,
) -> ExternalDocsValidationResult:
    """Validate indexed active external docs for a source."""
    matching_docs = [_doc for _doc in documents if _metadata(_doc).get("source_name") == source_name]
    active_docs = [_doc for _doc in matching_docs if _doc.get("status") == "active"]
    archived_docs = [_doc for _doc in matching_docs if _doc.get("status") == "archived"]
    active_ids = {str(_doc.get("id") or "") for _doc in active_docs}
    source_chunks = [_chunk for _chunk in chunks if str(_chunk.get("document_id") or "") in active_ids]
    doc_by_id = {str(_doc.get("id") or ""): _doc for _doc in active_docs}

    missing_url_docs = _docs_without_source_url(active_docs)
    duplicate_keys = _duplicate_active_keys(active_docs)
    source_labels_without_url = _source_labels_without_url(active_docs, sample_size=sample_size)

    raw_html_chunks = _matching_chunks(source_chunks, doc_by_id, _has_raw_html)
    nav_noise_chunks = _matching_chunks(source_chunks, doc_by_id, _has_nav_footer_noise)
    generator_noise_chunks = _matching_chunks(source_chunks, doc_by_id, _has_generator_boilerplate)
    empty_chunks = _matching_chunks(source_chunks, doc_by_id, lambda text: not text.strip())
    very_short_chunks = _matching_chunks(source_chunks, doc_by_id, _is_very_short)
    title_only_chunks = _matching_chunks(
        source_chunks,
        doc_by_id,
        lambda text, chunk=None: _is_title_only(text, str((chunk or {}).get("heading") or "")),
    )
    chunks_without_useful_text = _matching_chunks(source_chunks, doc_by_id, _lacks_useful_text)
    huge_chunks = _matching_chunks(source_chunks, doc_by_id, lambda text: len(text) > HUGE_CHUNK_CHARS)

    total_chunks = len(source_chunks)
    active_count = len(active_docs)
    archived_active_ratio = round(len(archived_docs) / active_count, 4) if active_count else 0.0
    code_blocks_count = sum(1 for chunk in source_chunks if "```" in str(chunk.get("content") or ""))
    technical_source = _looks_technical(active_docs, source_chunks)
    metrics: dict[str, int | float | bool] = {
        "active_docs": active_count,
        "archived_docs": len(archived_docs),
        "total_chunks": total_chunks,
        "missing_url_docs": len(missing_url_docs),
        "duplicate_active_versions": len(duplicate_keys),
        "raw_html_count": len(raw_html_chunks),
        "nav_footer_noise_count": len(nav_noise_chunks),
        "generator_boilerplate_count": len(generator_noise_chunks),
        "empty_chunks": len(empty_chunks),
        "very_short_chunks": len(very_short_chunks),
        "very_short_chunks_ratio": _ratio(len(very_short_chunks), total_chunks),
        "title_only_chunks": len(title_only_chunks),
        "title_only_chunks_ratio": _ratio(len(title_only_chunks), total_chunks),
        "chunks_without_useful_text": len(chunks_without_useful_text),
        "chunks_without_useful_text_ratio": _ratio(len(chunks_without_useful_text), total_chunks),
        "source_labels_without_url": len(source_labels_without_url),
        "code_blocks_count": code_blocks_count,
        "suspicious_huge_chunks": len(huge_chunks),
        "archived_active_ratio": archived_active_ratio,
        "technical_source": technical_source,
    }

    failures = _failures(metrics)
    warnings = _warnings(metrics)
    quality: QualityStatus = "FAIL" if failures else "WARN" if warnings else "PASS"
    return ExternalDocsValidationResult(
        source_name=source_name,
        quality=quality,
        metrics=metrics,
        failures=tuple(failures),
        warnings=tuple(warnings),
        samples={
            "missing_url_docs": _sample(missing_url_docs, sample_size),
            "duplicate_active_keys": _sample(duplicate_keys, sample_size),
            "raw_html_chunks": _sample(raw_html_chunks, sample_size),
            "nav_footer_noise_chunks": _sample(nav_noise_chunks, sample_size),
            "generator_boilerplate_chunks": _sample(generator_noise_chunks, sample_size),
            "empty_chunks": _sample(empty_chunks, sample_size),
            "very_short_chunks": _sample(very_short_chunks, sample_size),
            "title_only_chunks": _sample(title_only_chunks, sample_size),
            "chunks_without_useful_text": _sample(chunks_without_useful_text, sample_size),
            "source_labels_without_url": _sample(source_labels_without_url, sample_size),
            "suspicious_huge_chunks": _sample(huge_chunks, sample_size),
        },
    )


def _failures(metrics: dict[str, int | float | bool]) -> list[str]:
    result: list[str] = []
    checks = {
        "raw_html_count": "raw HTML markers found in chunks",
        "missing_url_docs": "active docs without source_url/canonical_url",
        "duplicate_active_versions": "duplicate active document_key/canonical_url",
        "empty_chunks": "empty chunks found",
        "source_labels_without_url": "source labels without URL",
    }
    for key, message in checks.items():
        if int(metrics.get(key, 0) or 0) > 0:
            result.append(message)
    return result


def _warnings(metrics: dict[str, int | float | bool]) -> list[str]:
    result: list[str] = []
    if int(metrics.get("active_docs", 0) or 0) == 0:
        result.append("source has no active docs")
    if float(metrics.get("title_only_chunks_ratio", 0.0) or 0.0) > TITLE_ONLY_WARN_RATIO:
        result.append("title-only chunk ratio is high")
    if float(metrics.get("very_short_chunks_ratio", 0.0) or 0.0) > VERY_SHORT_WARN_RATIO:
        result.append("very short chunk ratio is high")
    if float(metrics.get("chunks_without_useful_text_ratio", 0.0) or 0.0) > WITHOUT_USEFUL_TEXT_WARN_RATIO:
        result.append("chunks without useful text ratio is high")
    if float(metrics.get("archived_active_ratio", 0.0) or 0.0) > ARCHIVED_ACTIVE_WARN_RATIO:
        result.append("archived/active ratio is high")
    if int(metrics.get("nav_footer_noise_count", 0) or 0) > 0:
        result.append("navigation/footer/cookie markers found")
    if int(metrics.get("generator_boilerplate_count", 0) or 0) > 0:
        result.append("generator boilerplate found")
    if int(metrics.get("suspicious_huge_chunks", 0) or 0) > 0:
        result.append("suspicious huge chunks found")
    if bool(metrics.get("technical_source")) and int(metrics.get("code_blocks_count", 0) or 0) == 0:
        result.append("technical docs source has no code blocks")
    return result


def _docs_without_source_url(docs: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for doc in docs:
        metadata = _metadata(doc)
        if metadata.get("source_url") or metadata.get("canonical_url"):
            continue
        result.append(_doc_label(doc))
    return result


def _duplicate_active_keys(docs: list[dict[str, Any]]) -> list[str]:
    keys = [_stable_key(doc) for doc in docs]
    counts = Counter(key for key in keys if key)
    return [key for key, count in counts.items() if count > 1]


def _source_labels_without_url(docs: list[dict[str, Any]], *, sample_size: int) -> list[str]:
    label_builder = SourceLabelBuilder()
    result: list[str] = []
    for doc in docs:
        metadata = _metadata(doc)
        source_uri = str(metadata.get("canonical_url") or metadata.get("source_url") or "").strip()
        source = SourceRef(
            document_id=str(doc.get("id") or ""),
            document_title=str(doc.get("title") or ""),
            source_uri=source_uri,
            metadata={
                **metadata,
                "filename": doc.get("filename"),
                "title": doc.get("title"),
            },
        )
        label = label_builder.build(source)
        if "http://" not in label and "https://" not in label:
            result.append(_doc_label(doc))
        if len(result) >= sample_size:
            break
    return result


def _matching_chunks(
    chunks: list[dict[str, Any]],
    doc_by_id: dict[str, dict[str, Any]],
    predicate: Any,
) -> list[str]:
    result: list[str] = []
    for chunk in chunks:
        content = str(chunk.get("content") or "")
        try:
            matches = predicate(content, chunk)
        except TypeError:
            matches = predicate(content)
        if matches:
            doc = doc_by_id.get(str(chunk.get("document_id") or ""), {})
            result.append(f"{_doc_label(doc)} #{chunk.get('chunk_index')}")
    return result


def _has_raw_html(text: str) -> bool:
    return bool(RAW_HTML_RE.search(without_fenced_code(text)))


def _has_nav_footer_noise(text: str) -> bool:
    lowered = text.casefold()
    return bool(
        any(marker in lowered for marker in NAV_FOOTER_MARKERS)
        or any(PREVIOUS_NEXT_RE.search(line) for line in text.splitlines())
    )


def _has_generator_boilerplate(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in GENERATOR_MARKERS)


def _is_very_short(text: str) -> bool:
    if has_protected_technical_content(text):
        return False
    return 0 < _useful_word_count(text) < VERY_SHORT_USEFUL_WORDS


def _is_title_only(text: str, heading: str) -> bool:
    if has_protected_technical_content(text):
        return False
    if not heading.strip():
        return False
    normalized_text = _normalize_for_compare(text)
    normalized_heading = _normalize_for_compare(heading)
    if not normalized_text or not normalized_heading:
        return False
    if normalized_text == normalized_heading:
        return True
    without_heading = normalized_text.replace(normalized_heading, " ").strip()
    return bool(not without_heading or _useful_word_count(without_heading) < MIN_USEFUL_WORDS)


def _lacks_useful_text(text: str) -> bool:
    if not text.strip():
        return True
    if has_protected_technical_content(text):
        return False
    return _useful_word_count(text) < MIN_USEFUL_WORDS


def _looks_technical(docs: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> bool:
    if len(docs) < 3 and len(chunks) < 10:
        return False
    text = " ".join(
        [
            *(str(doc.get("title") or "") for doc in docs[:25]),
            *(str(chunk.get("heading") or "") for chunk in chunks[:50]),
            *(str(chunk.get("content") or "")[:400] for chunk in chunks[:20]),
        ]
    ).casefold()
    return any(marker in text for marker in TECHNICAL_SOURCE_MARKERS)


def _stable_key(doc: dict[str, Any]) -> str:
    metadata = _metadata(doc)
    return str(metadata.get("canonical_url") or doc.get("document_key") or "").strip()


def _doc_label(doc: dict[str, Any]) -> str:
    return str(doc.get("title") or doc.get("filename") or doc.get("document_key") or doc.get("id") or "unknown")


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _sample(items: list[str], sample_size: int) -> list[str]:
    return items[: max(sample_size, 0)]


def _ratio(value: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(value / total, 4)


def _normalize_for_compare(text: str) -> str:
    clean = re.sub(r"^#+\s*", "", str(text or "").strip(), flags=re.MULTILINE)
    clean = re.sub(r"\s+", " ", clean).strip(" -:;,.")
    return clean.casefold()


def _useful_word_count(text: str) -> int:
    return len([token for token in TOKEN_RE.findall(text) if len(token.strip("#.")) >= 2])
