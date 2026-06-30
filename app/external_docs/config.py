"""External documentation whitelist configuration."""

from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import urlparse
from typing import Any

from app.external_docs.types import ExternalDocsConfig, ExternalDocSource

DEFAULT_EXTERNAL_DOCS_CONFIG = Path("config/external_docs.yaml")


class ExternalDocsConfigError(ValueError):
    """Raised when the external docs whitelist config is invalid."""


def load_external_docs_config(path: Path | str = DEFAULT_EXTERNAL_DOCS_CONFIG) -> ExternalDocsConfig:
    """Load and validate external docs sources from a small YAML file."""
    config_path = Path(path)
    if not config_path.exists():
        raise ExternalDocsConfigError(f"External docs config not found: {config_path}")
    data = _parse_sources_yaml(config_path.read_text(encoding="utf-8"))
    sources = tuple(_source_from_mapping(row, index=index + 1) for index, row in enumerate(data))
    names = [source.name for source in sources]
    if len(names) != len(set(names)):
        raise ExternalDocsConfigError("External docs config contains duplicate source names.")
    if not sources:
        raise ExternalDocsConfigError("External docs config must contain at least one source.")
    return ExternalDocsConfig(sources=sources)


def _source_from_mapping(row: dict[str, Any], *, index: int) -> ExternalDocSource:
    name = _required_str(row, "name", index=index)
    source_kind = str(row.get("source_kind") or "external_docs").strip()
    allowed_domains = tuple(_required_list(row, "allowed_domains", index=index))
    start_urls = tuple(_required_list(row, "start_urls", index=index))
    allow_patterns = tuple(str(item) for item in row.get("allow_patterns", ()) if str(item).strip())
    deny_patterns = tuple(str(item) for item in row.get("deny_patterns", ()) if str(item).strip())
    crawl_depth = _int(row.get("crawl_depth"), default=1)
    max_pages = _int(row.get("max_pages"), default=20)
    refresh_days = _int(row.get("refresh_days"), default=14)

    if source_kind != "external_docs":
        raise ExternalDocsConfigError(f"Source {name}: source_kind must be external_docs.")
    if crawl_depth < 0:
        raise ExternalDocsConfigError(f"Source {name}: crawl_depth must be >= 0.")
    if max_pages <= 0:
        raise ExternalDocsConfigError(f"Source {name}: max_pages must be > 0.")
    if refresh_days <= 0:
        raise ExternalDocsConfigError(f"Source {name}: refresh_days must be > 0.")

    normalized_domains = tuple(_normalize_domain(domain, source_name=name) for domain in allowed_domains)
    for url in start_urls:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ExternalDocsConfigError(f"Source {name}: invalid start_url: {url}")
        host = parsed.hostname or ""
        if not _host_allowed(host, normalized_domains):
            raise ExternalDocsConfigError(f"Source {name}: start_url is outside allowed_domains: {url}")
    for pattern in (*allow_patterns, *deny_patterns):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ExternalDocsConfigError(f"Source {name}: invalid regex pattern {pattern!r}: {exc}") from exc

    return ExternalDocSource(
        name=name,
        source_kind=source_kind,
        allowed_domains=normalized_domains,
        start_urls=start_urls,
        allow_patterns=allow_patterns,
        deny_patterns=deny_patterns,
        crawl_depth=crawl_depth,
        max_pages=max_pages,
        refresh_days=refresh_days,
    )


def _parse_sources_yaml(text: str) -> list[dict[str, Any]]:
    """Parse the limited YAML subset used by config/external_docs.yaml."""
    sources: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_list_key: str | None = None
    seen_sources_key = False

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        raw_without_comment = raw_line.split("#", 1)[0].rstrip()
        if not raw_without_comment.strip():
            continue
        indent = len(raw_without_comment) - len(raw_without_comment.lstrip(" "))
        line = raw_without_comment.strip()
        if line == "sources:":
            seen_sources_key = True
            continue
        if not seen_sources_key:
            raise ExternalDocsConfigError("External docs config must start with sources:.")

        if line.startswith("- "):
            item = line[2:].strip()
            if indent == 2:
                if current is not None:
                    sources.append(current)
                current = {}
                current_list_key = None
                if item:
                    key, value = _split_key_value(item, line_number)
                    current[key] = _scalar(value)
                continue
            if current is None or current_list_key is None:
                raise ExternalDocsConfigError(f"Line {line_number}: list item has no parent key.")
            current[current_list_key].append(_scalar(item))
            continue

        if current is None:
            raise ExternalDocsConfigError(f"Line {line_number}: source field appears before a source item.")
        key, value = _split_key_value(line, line_number)
        if value == "":
            current[key] = []
            current_list_key = key
        else:
            current[key] = _scalar(value)
            current_list_key = None

    if current is not None:
        sources.append(current)
    return sources


def _split_key_value(text: str, line_number: int) -> tuple[str, str]:
    if ":" not in text:
        raise ExternalDocsConfigError(f"Line {line_number}: expected key: value.")
    key, value = text.split(":", 1)
    key = key.strip()
    if not key:
        raise ExternalDocsConfigError(f"Line {line_number}: empty key.")
    return key, value.strip()


def _scalar(value: str) -> object:
    clean = value.strip()
    if len(clean) >= 2 and clean[:1] == clean[-1:] and clean[:1] in {"'", '"'}:
        clean = clean[1:-1]
    clean = clean.replace("\\\\", "\\")
    if clean.isdigit():
        return int(clean)
    return clean


def _required_str(row: dict[str, Any], key: str, *, index: int) -> str:
    value = str(row.get(key) or "").strip()
    if not value:
        raise ExternalDocsConfigError(f"Source #{index}: missing {key}.")
    return value


def _required_list(row: dict[str, Any], key: str, *, index: int) -> list[str]:
    value = row.get(key)
    if not isinstance(value, list) or not value:
        raise ExternalDocsConfigError(f"Source #{index}: {key} must be a non-empty list.")
    result = [str(item).strip() for item in value if str(item).strip()]
    if not result:
        raise ExternalDocsConfigError(f"Source #{index}: {key} must contain non-empty values.")
    return result


def _int(value: object, *, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ExternalDocsConfigError(f"Expected integer value, got {value!r}.") from exc


def _normalize_domain(value: str, *, source_name: str) -> str:
    clean = value.strip().lower()
    if not clean or "/" in clean or ":" in clean:
        raise ExternalDocsConfigError(f"Source {source_name}: invalid allowed domain: {value}")
    return clean


def _host_allowed(host: str, domains: tuple[str, ...]) -> bool:
    clean = host.lower()
    return any(clean == domain or clean.endswith("." + domain) for domain in domains)
