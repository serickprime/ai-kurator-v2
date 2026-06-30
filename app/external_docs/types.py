"""Shared types for whitelisted external documentation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


EXTERNAL_DOCS_VERSION = "external-docs-v1"


@dataclass(frozen=True)
class ExternalDocSource:
    """One whitelisted external documentation source."""

    name: str
    source_kind: str
    allowed_domains: tuple[str, ...]
    start_urls: tuple[str, ...]
    allow_patterns: tuple[str, ...] = ()
    deny_patterns: tuple[str, ...] = ()
    crawl_depth: int = 1
    max_pages: int = 20
    refresh_days: int = 14


@dataclass(frozen=True)
class ExternalDocsConfig:
    """Loaded external docs configuration."""

    sources: tuple[ExternalDocSource, ...]

    def source(self, name: str) -> ExternalDocSource:
        """Return one source by name."""
        for source in self.sources:
            if source.name == name:
                return source
        raise KeyError(name)


@dataclass(frozen=True)
class CrawledPage:
    """Fetched HTML page from a whitelisted source."""

    source_name: str
    url: str
    html: str
    status_code: int
    content_type: str
    fetched_at: datetime
    depth: int = 0


@dataclass(frozen=True)
class ExtractedPage:
    """Clean page text ready for indexing."""

    source_name: str
    source_url: str
    canonical_url: str
    title: str
    structured_text: str
    content_hash: str
    headings: tuple[str, ...] = ()
    crawled_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ExternalDocsIndexResult:
    """Result of indexing one extracted external page."""

    source_name: str
    url: str
    document_id: str = ""
    document_key: str = ""
    version: int = 0
    skipped: bool = False
    archived_old: bool = False
    sections_count: int = 0
    chunks_count: int = 0
    error: str = ""


@dataclass
class ExternalDocsSyncStats:
    """Aggregate sync counters for CLI output."""

    source_name: str
    domains: tuple[str, ...]
    fetched: int = 0
    skipped_unchanged: int = 0
    indexed_new: int = 0
    archived_old: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def add(self, result: ExternalDocsIndexResult) -> None:
        """Update counters from one page index result."""
        if result.error:
            self.failed += 1
            self.errors.append(result.error[:300])
        elif result.skipped:
            self.skipped_unchanged += 1
        else:
            self.indexed_new += 1
        if result.archived_old:
            self.archived_old += 1

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe summary."""
        return {
            "source_name": self.source_name,
            "domains": list(self.domains),
            "fetched": self.fetched,
            "skipped_unchanged": self.skipped_unchanged,
            "indexed_new": self.indexed_new,
            "archived_old": self.archived_old,
            "failed": self.failed,
            "errors": self.errors[:10],
        }
