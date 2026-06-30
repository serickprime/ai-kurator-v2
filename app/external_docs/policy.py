"""Safety policy for external docs crawling and local-first RAG use."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from app.external_docs.types import ExternalDocSource

FORBIDDEN_PATH_SEGMENTS = {
    "account",
    "admin",
    "dashboard",
    "login",
    "logout",
    "me",
    "profile",
    "settings",
    "signin",
    "signup",
    "user",
    "users",
}

BINARY_EXTENSIONS = {
    ".7z",
    ".avi",
    ".bin",
    ".bmp",
    ".css",
    ".csv",
    ".doc",
    ".docx",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".svg",
    ".tar",
    ".webp",
    ".xls",
    ".xlsx",
    ".zip",
}

FRESHNESS_MARKERS = (
    "latest",
    "current",
    "new version",
    "new docs",
    "official docs",
    "documentation",
    "changelog",
    "release",
    "сейчас",
    "актуальн",
    "последн",
    "новой версии",
    "официальн",
    "документац",
)


def is_url_allowed(source: ExternalDocSource, url: str) -> bool:
    """Return true when a URL is inside the source whitelist and safe to crawl."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if not host or not any(host == domain or host.endswith("." + domain) for domain in source.allowed_domains):
        return False
    path = parsed.path or "/"
    lowered_path = path.lower()
    if any(segment in FORBIDDEN_PATH_SEGMENTS for segment in _path_segments(lowered_path)):
        return False
    if any(lowered_path.endswith(extension) for extension in BINARY_EXTENSIONS):
        return False
    if source.allow_patterns and not any(re.search(pattern, url) for pattern in source.allow_patterns):
        return False
    if source.deny_patterns and any(re.search(pattern, url) for pattern in source.deny_patterns):
        return False
    return True


def freshness_required(question: str) -> bool:
    """Return true when the user asks for current or official documentation."""
    lowered = question.casefold()
    return any(marker in lowered for marker in FRESHNESS_MARKERS)


def local_evidence_is_sufficient(evidence_pack: object | None) -> bool:
    """Return true when local evidence is enough and external docs should not override it."""
    if evidence_pack is None:
        return False
    answer_mode = str(getattr(evidence_pack, "answer_mode", "") or "")
    items = tuple(getattr(evidence_pack, "items", ()) or ())
    missing = tuple(getattr(evidence_pack, "missing_requirements", ()) or ())
    return answer_mode == "answer_from_materials" and bool(items) and not missing


def should_use_external_docs(analysis: object, local_evidence_pack: object | None = None) -> bool:
    """Return true when external docs may be used after local evidence is checked."""
    if local_evidence_is_sufficient(local_evidence_pack):
        return False
    if bool(getattr(analysis, "needs_external_docs", False)) or bool(getattr(analysis, "needs_official_docs", False)):
        return True
    if bool(getattr(analysis, "freshness_required", False)):
        return True
    expected = set(getattr(analysis, "expected_content_types", ()) or ())
    return bool(expected & {"official_docs", "external_docs"})


def _path_segments(path: str) -> list[str]:
    return [segment for segment in path.strip("/").split("/") if segment]
