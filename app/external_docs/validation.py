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

HTML_TAG_RE = re.compile(r"<\s*(/)?\s*([A-Za-z][A-Za-z0-9:-]*)([^<>]*?)(/)?\s*>", re.IGNORECASE)
HTML_SIGNAL_RE = re.compile(r"<\s*/?\s*[A-Za-z][A-Za-z0-9:-]*(?:\s[^<>]*)?/?>|class\s*=|data-[\w-]+\s*=", re.IGNORECASE)
PLACEHOLDER_TOKEN_RE = re.compile(
    r"<\s*([A-Za-z][A-Za-z0-9_-]*(?:\s+[A-Za-z][A-Za-z0-9_-]*){0,2})\s*>"
)
INLINE_CODE_BACKTICK_RE = re.compile(r"`+")
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
SAFE_INLINE_HTML_TAGS = {
    "a",
    "aside",
    "audio",
    "br",
    "b",
    "blockquote",
    "cite",
    "code",
    "del",
    "details",
    "dd",
    "dl",
    "dt",
    "em",
    "figcaption",
    "figure",
    "caption",
    "col",
    "colgroup",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "input",
    "ins",
    "li",
    "mark",
    "ol",
    "p",
    "picture",
    "pre",
    "q",
    "s",
    "small",
    "source",
    "span",
    "strike",
    "strong",
    "sub",
    "summary",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "time",
    "track",
    "tr",
    "tg-spoiler",
    "u",
    "ul",
    "video",
}
STRUCTURAL_PAGE_TAGS = {
    "article",
    "body",
    "div",
    "footer",
    "head",
    "header",
    "html",
    "main",
    "nav",
    "section",
}
DANGEROUS_HTML_TAGS = {
    "button",
    "canvas",
    "embed",
    "fieldset",
    "form",
    "iframe",
    "label",
    "link",
    "meta",
    "noscript",
    "object",
    "script",
    "select",
    "style",
    "svg",
    "template",
    "textarea",
}
VOID_HTML_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
SAFE_HTML_ATTRS = {
    "alt",
    "cite",
    "datetime",
    "dir",
    "emoji-id",
    "expandable",
    "format",
    "colspan",
    "height",
    "href",
    "item-id",
    "lang",
    "long",
    "lat",
    "name",
    "open",
    "reversed",
    "rowspan",
    "scope",
    "src",
    "start",
    "title",
    "type",
    "unix",
    "value",
    "valign",
    "width",
    "zoom",
}
SAFE_STRUCTURAL_EXAMPLE_TAGS = {"footer"}
SAFE_TABLE_ALIGN_TAGS = {"td", "th"}
SAFE_TABLE_ALIGN_VALUES = {"left", "center", "right", "justify", "char"}
FORBIDDEN_HTML_ATTRS = {"contenteditable", "draggable", "hidden", "role", "srcset", "style", "tabindex"}
SAFE_URL_ATTRS = {"cite", "href", "src"}
SAFE_CLASS_TAGS = {"span"}
SAFE_LANGUAGE_CLASS_TAGS = {"code", "pre"}
SAFE_BOOLEAN_HTML_ATTRS = {
    "bordered",
    "expandable",
    "open",
    "reversed",
    "striped",
}
SAFE_CUSTOM_BOOLEAN_ATTR_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+")
UNSAFE_CLASS_TOKENS = {
    "button",
    "container",
    "content",
    "cookie",
    "drawer",
    "footer",
    "grid",
    "header",
    "layout",
    "menu",
    "nav",
    "navigation",
    "page",
    "sidebar",
    "screenshot",
}
MAX_DOCUMENTED_HTML_TAGS = 80
PLACEHOLDER_CONTEXT_RE = re.compile(
    r"https?://|/[A-Za-z0-9_<]|curl\b|command\b|endpoint\b|header\b|"
    r"\burl\b|\bpath\b|\bparameter\b|\btemplate\b|\bplaceholder\b|"
    r"\bsubstitut\w*\b|\breplac\w*\b|\btoken\b|\bslug\b|\bttl\b|"
    r"\bvalue\b|\bquery\b|\brequest\b|\bresponse\b|\bcache\b|"
    r"\bcertificate\b|\bworkspace\b|\bsettings\b|\blabeled\b|"
    r"[A-Za-z0-9_.-]+\s*=",
    re.IGNORECASE,
)
PLACEHOLDER_PROSE_BEFORE_RE = re.compile(
    r"(?:"
    r"\buse(?:\s+\w+){0,3}|"
    r"\bshown\s+as|"
    r"\brefer(?:red)?\s+to(?:\s+\w+){0,4}\s+as|"
    r"\breplace(?:\s+\w+){0,6}\s+with|"
    r"\bsubstitute(?:\s+\w+){0,6}\s+with|"
    r"\blabel(?:ed)?(?:\s+\w+){0,4}\s+as"
    r")\s*$",
    re.IGNORECASE,
)
PLACEHOLDER_PROSE_AFTER_RE = re.compile(
    r"^\s*(?:"
    r"in\s+(?:this\s+)?(?:document|example|guide|reference)|"
    r"for\s+(?:this\s+)?(?:example|placeholder|template)|"
    r"as\s+(?:a\s+)?(?:placeholder|template|identifier|value)"
    r")\b",
    re.IGNORECASE,
)
PLACEHOLDER_SEPARATOR_RE = re.compile(r"^\s*/\s*|\s*/\s*$")
ESCAPED_COMPARISON_RE = re.compile(
    r"<\s+(?:and|or)\s+>|<\s+(?:with|as|than|to|from|before|after)\s+&(?:lt|gt|amp);[^<>]{0,80}>",
    re.IGNORECASE,
)


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
    prose = remove_markdown_code_for_validation(text)
    prose = _strip_safe_placeholder_tokens(prose)
    prose = _strip_safe_escaped_comparison_fragments(prose)
    if not HTML_SIGNAL_RE.search(prose):
        return False
    return not _looks_like_documented_html_example(prose)


def remove_markdown_code_for_validation(text: str) -> str:
    """Remove fenced and inline Markdown code spans before prose validation."""
    return _without_inline_code_spans(without_fenced_code(text))


def _has_nav_footer_noise(text: str) -> bool:
    lowered = text.casefold()
    return bool(
        any(marker in lowered for marker in NAV_FOOTER_MARKERS)
        or any(PREVIOUS_NEXT_RE.search(line) for line in text.splitlines())
    )


def _has_generator_boilerplate(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in GENERATOR_MARKERS)


def _looks_like_documented_html_example(text: str) -> bool:
    parsed_tags = _parse_html_tags(text)
    if not parsed_tags:
        return False
    if len(parsed_tags) > MAX_DOCUMENTED_HTML_TAGS:
        return False
    if re.search(r"\b(?:class|data-[\w-]+)\s*=", _strip_html_tags(text), flags=re.IGNORECASE):
        return False
    stack: list[_ParsedHtmlTag] = []
    isolated_mentions: list[_ParsedHtmlTag] = []
    documented_context = _has_documented_markup_context(text)
    saw_safe_documented_fragment = False
    for tag in parsed_tags:
        if tag.name in DANGEROUS_HTML_TAGS:
            return False
        if tag.closing:
            if not stack or stack[-1].name != tag.name:
                if (documented_context or saw_safe_documented_fragment) and _is_safe_closing_tag_mention(tag):
                    isolated_mentions.append(tag)
                    continue
                return False
            stack.pop()
            continue
        if tag.name in STRUCTURAL_PAGE_TAGS:
            if _is_safe_structural_documented_example(text, tag, documented_context):
                saw_safe_documented_fragment = True
                if tag.self_closing:
                    continue
                stack.append(tag)
                continue
            if tag.attrs or not _is_isolated_tag_mention(text, tag):
                return False
            continue
        if not _is_safe_documented_tag(tag):
            return False
        saw_safe_documented_fragment = True
        if tag.self_closing or tag.name in VOID_HTML_TAGS:
            continue
        if _is_isolated_tag_mention(text, tag) or (documented_context and _is_tag_list_mention(text, tag)):
            isolated_mentions.append(tag)
            continue
        stack.append(tag)
    return all(_is_unclosed_safe_documented_mention(text, tag, documented_context) for tag in stack) and all(
        _is_safe_documented_tag(tag) for tag in isolated_mentions
    )


@dataclass(frozen=True)
class _ParsedHtmlTag:
    name: str
    attrs_text: str
    attrs: dict[str, str | None]
    closing: bool
    self_closing: bool
    start: int
    end: int


def _without_inline_code_spans(text: str) -> str:
    """Remove Markdown inline code spans, including multi-backtick spans."""
    value = str(text or "")
    result: list[str] = []
    index = 0
    while index < len(value):
        match = INLINE_CODE_BACKTICK_RE.search(value, index)
        if not match:
            result.append(value[index:])
            break
        result.append(value[index : match.start()])
        ticks = match.group(0)
        end = value.find(ticks, match.end())
        if end < 0:
            result.append(value[match.start() :])
            break
        result.append(" ")
        index = end + len(ticks)
    return "".join(result)


def _parse_html_tags(text: str) -> list[_ParsedHtmlTag]:
    tags: list[_ParsedHtmlTag] = []
    for match in HTML_TAG_RE.finditer(text):
        attrs_text = match.group(3) or ""
        attrs = _parse_html_attrs(attrs_text)
        if attrs is None:
            return []
        tags.append(
            _ParsedHtmlTag(
                name=match.group(2).casefold(),
                attrs_text=attrs_text,
                attrs=attrs,
                closing=bool(match.group(1)),
                self_closing=bool(match.group(4)) or attrs_text.rstrip().endswith("/"),
                start=match.start(),
                end=match.end(),
            )
        )
    return tags


def _parse_html_attrs(attrs_text: str) -> dict[str, str | None] | None:
    attrs: dict[str, str | None] = {}
    rest = attrs_text.strip().removesuffix("/").strip()
    while rest:
        match = re.match(r"([A-Za-z_:][A-Za-z0-9_.:-]*)(?:\s*=\s*(\"[^\"]*\"|'[^']*'))?", rest)
        if not match:
            return None
        name = match.group(1).casefold()
        raw_value = match.group(2)
        attrs[name] = raw_value[1:-1] if raw_value is not None else None
        rest = rest[match.end() :].strip()
    return attrs


def _strip_safe_placeholder_tokens(text: str) -> str:
    return PLACEHOLDER_TOKEN_RE.sub(
        lambda match: " " if _looks_like_placeholder_token(text, match) else match.group(0),
        text,
    )


def _looks_like_placeholder_token(text: str, match: re.Match[str]) -> bool:
    token = " ".join(match.group(1).split())
    token_key = token.casefold()
    if len(token) > 60:
        return False
    if "=" in match.group(0) or '"' in match.group(0) or "'" in match.group(0):
        return False
    if token_key in STRUCTURAL_PAGE_TAGS or token_key in DANGEROUS_HTML_TAGS or token_key in SAFE_INLINE_HTML_TAGS:
        return False
    if token_key in VOID_HTML_TAGS:
        return False
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*(?:\s+[A-Za-z][A-Za-z0-9_-]*){0,2}", token):
        return False
    if _has_matching_closing_tag(text, token_key, match.end()):
        return False
    return _has_placeholder_context(text, match)


def _has_matching_closing_tag(text: str, tag_name: str, start: int) -> bool:
    return bool(re.search(rf"</\s*{re.escape(tag_name)}\s*>", text[start:], flags=re.IGNORECASE))


def _has_placeholder_context(text: str, match: re.Match[str]) -> bool:
    before = text[max(0, match.start() - 90) : match.start()]
    after = text[match.end() : match.end() + 90]
    context = f"{before} {after}"
    if PLACEHOLDER_CONTEXT_RE.search(context):
        return True
    if _has_placeholder_prose_context(before, after):
        return True
    return bool(PLACEHOLDER_SEPARATOR_RE.search(before) or PLACEHOLDER_SEPARATOR_RE.match(after))


def _has_placeholder_prose_context(before: str, after: str) -> bool:
    before_window = _normalize_context_window(before[-90:]).strip(" :;,-").casefold()
    after_window = _normalize_context_window(after[:90]).casefold()
    if not PLACEHOLDER_PROSE_BEFORE_RE.search(before_window):
        return False
    return bool(
        PLACEHOLDER_PROSE_AFTER_RE.match(after_window)
        or re.search(r"\b(?:replace|substitute|shown\s+as|refer(?:red)?\s+to|label(?:ed)?)\b", before_window)
    )


def _strip_safe_escaped_comparison_fragments(text: str) -> str:
    return ESCAPED_COMPARISON_RE.sub(" ", text)


def _is_safe_documented_tag(tag: _ParsedHtmlTag) -> bool:
    if tag.name not in SAFE_INLINE_HTML_TAGS and not _is_safe_custom_element_name(tag.name):
        return False
    return _attrs_are_safe_for_documented_example(tag)


def _is_safe_custom_element_name(name: str) -> bool:
    return bool(
        "-" in name
        and len(name) <= 40
        and re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+", name)
    )


def _attrs_are_safe_for_documented_example(tag: _ParsedHtmlTag) -> bool:
    if tag.name == "input" and tag.attrs != {"type": "checkbox"}:
        return False
    for name, value in tag.attrs.items():
        if name.startswith("on") or name.startswith("data-") or name.startswith("aria-"):
            return False
        if name in FORBIDDEN_HTML_ATTRS:
            return False
        if name == "class":
            if value is None:
                return False
            if tag.name in SAFE_LANGUAGE_CLASS_TAGS and _is_safe_language_class(value):
                continue
            if tag.name not in SAFE_CLASS_TAGS or not _is_safe_example_class(value):
                return False
            continue
        if name == "align":
            if value is None or tag.name not in SAFE_TABLE_ALIGN_TAGS or not _is_safe_table_align_value(value):
                return False
            continue
        if value is None and _is_safe_boolean_attr_name(name):
            continue
        if name not in SAFE_HTML_ATTRS:
            return False
        if value is None:
            if not _is_safe_boolean_attr_name(name):
                return False
            continue
        if not _is_safe_attr_value(name, value):
            return False
    return True


def _is_safe_example_class(value: str) -> bool:
    classes = [item for item in re.split(r"\s+", value.casefold().strip()) if item]
    if not classes or len(classes) > 2:
        return False
    for class_name in classes:
        if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+", class_name):
            return False
        if any(token in class_name for token in UNSAFE_CLASS_TOKENS):
            return False
    return True


def _is_safe_language_class(value: str) -> bool:
    clean = value.strip()
    return bool(re.fullmatch(r"language-[A-Za-z0-9_.+-]{1,50}", clean))


def _is_safe_boolean_attr_name(name: str) -> bool:
    return name in SAFE_BOOLEAN_HTML_ATTRS or bool(SAFE_CUSTOM_BOOLEAN_ATTR_RE.fullmatch(name))


def _is_safe_attr_value(name: str, value: str) -> bool:
    clean = value.strip()
    if len(clean) > 240 or any(char in clean for char in "<>`"):
        return False
    if name in SAFE_URL_ATTRS and re.match(r"\s*(?:javascript|data|vbscript):", clean, flags=re.IGNORECASE):
        return False
    if name in {"height", "lat", "long", "start", "unix", "width", "zoom"}:
        return bool(re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", clean))
    if name in {"dir"}:
        return clean in {"auto", "ltr", "rtl"}
    if name == "type":
        return bool(re.fullmatch(r"[A-Za-z0-9_./+-]{1,80}", clean))
    if name in {"scope", "valign"}:
        return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,40}", clean))
    return True


def _is_safe_table_align_value(value: str) -> bool:
    return value.strip().casefold() in SAFE_TABLE_ALIGN_VALUES


def _is_isolated_tag_mention(text: str, tag: _ParsedHtmlTag) -> bool:
    if tag.closing or tag.attrs or tag.self_closing:
        return False
    before = text[max(0, tag.start - 40) : tag.start].casefold()
    after = text[tag.end : tag.end + 40].casefold()
    return bool(
        re.search(r"\b(?:element|tag|tags|markup|html)\s+(?:called|named)?\s*$", before)
        or re.match(r"\s*(?:element|tag|tags|markup|html)\b", after)
    )


def _has_documented_markup_context(text: str) -> bool:
    lowered = text.casefold()
    return bool(
        re.search(
            r"\b(?:html|markup|tag|tags|element|elements|syntax|formatting|"
            r"example|examples|supports|supported|corresponding to|rich block|"
            r"rich text|preformatted|table|blockquote|disclosure)\b",
            lowered,
        )
    )


def _is_tag_list_mention(text: str, tag: _ParsedHtmlTag) -> bool:
    if tag.attrs or tag.self_closing:
        return False
    before = text[max(0, tag.start - 60) : tag.start].casefold()
    after = text[tag.end : tag.end + 60].casefold()
    if re.search(r"\b(?:tag|tags|element|elements|syntax|formatting|corresponding to)\b", before + " " + after):
        return True
    return bool(re.match(r"\s*(?:,|/|\bor\b|\band\b|\.|\)|:)", after))


def _is_safe_closing_tag_mention(tag: _ParsedHtmlTag) -> bool:
    pseudo_tag = _ParsedHtmlTag(
        name=tag.name,
        attrs_text="",
        attrs={},
        closing=False,
        self_closing=False,
        start=tag.start,
        end=tag.end,
    )
    return _is_safe_documented_tag(pseudo_tag)


def _is_safe_structural_documented_example(
    text: str,
    tag: _ParsedHtmlTag,
    documented_context: bool,
) -> bool:
    if tag.name not in SAFE_STRUCTURAL_EXAMPLE_TAGS or not documented_context or tag.attrs:
        return False
    close_match = re.search(rf"</\s*{re.escape(tag.name)}\s*>", text[tag.end :], flags=re.IGNORECASE)
    if not close_match:
        return False
    fragment_end = tag.end + close_match.end()
    if fragment_end - tag.start > 240:
        return False
    inner = text[tag.end : tag.end + close_match.start()]
    if HTML_TAG_RE.search(inner):
        return False
    return bool(inner.strip())


def _is_unclosed_safe_documented_mention(text: str, tag: _ParsedHtmlTag, documented_context: bool) -> bool:
    if _is_isolated_tag_mention(text, tag):
        return True
    return documented_context and _is_tag_list_mention(text, tag)


def _strip_html_tags(text: str) -> str:
    return HTML_TAG_RE.sub(" ", text)


def _normalize_context_window(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


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
