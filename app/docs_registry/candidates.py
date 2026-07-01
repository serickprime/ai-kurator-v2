"""Load curated official docs source candidates."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from app.docs_registry.models import DocsSourceCandidate, DocsSourceCandidatesConfig, RiskLevel

DEFAULT_DOCS_SOURCE_CANDIDATES_CONFIG = Path("config/docs_source_candidates.yaml")
ALLOWED_RISK_LEVELS: set[str] = {"low", "medium", "review"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class DocsSourceCandidatesConfigError(ValueError):
    """Raised when docs source candidates config is invalid."""


def load_docs_source_candidates_config(
    path: Path | str = DEFAULT_DOCS_SOURCE_CANDIDATES_CONFIG,
) -> DocsSourceCandidatesConfig:
    """Load and validate curated docs source candidates."""
    config_path = Path(path)
    if not config_path.exists():
        raise DocsSourceCandidatesConfigError(f"Docs source candidates config not found: {config_path}")
    rows = _parse_candidates_yaml(config_path.read_text(encoding="utf-8"))
    candidates = tuple(_candidate_from_mapping(row, index=index + 1) for index, row in enumerate(rows))
    if not candidates:
        raise DocsSourceCandidatesConfigError("Docs source candidates config must contain at least one candidate.")

    service_ids = [candidate.service_id for candidate in candidates]
    if len(service_ids) != len(set(service_ids)):
        raise DocsSourceCandidatesConfigError("Docs source candidates config contains duplicate service_id values.")

    docs_sources = [candidate.docs_source for candidate in candidates]
    if len(docs_sources) != len(set(docs_sources)):
        raise DocsSourceCandidatesConfigError("Docs source candidates config contains duplicate docs_source values.")

    return DocsSourceCandidatesConfig(candidates=candidates)


def _candidate_from_mapping(row: dict[str, Any], *, index: int) -> DocsSourceCandidate:
    service_id = _required_str(row, "service_id", index=index).casefold()
    display_name = _required_str(row, "display_name", index=index)
    aliases = tuple(_required_list(row, "aliases", index=index))
    docs_source = _required_str(row, "docs_source", index=index).casefold()
    official_start_urls = tuple(_required_list(row, "official_start_urls", index=index))
    allowed_domains = tuple(
        _normalize_domain(domain, candidate=service_id)
        for domain in _required_list(row, "allowed_domains", index=index)
    )
    allow_patterns = tuple(str(item) for item in row.get("allow_patterns", ()) if str(item).strip())
    deny_patterns = tuple(str(item) for item in row.get("deny_patterns", ()) if str(item).strip())
    max_pages = _int(row.get("max_pages"), default=20)
    crawl_depth = _int(row.get("crawl_depth"), default=1)
    risk_level = _required_str(row, "risk_level", index=index)
    notes = str(row.get("notes") or "").strip()

    if not ID_RE.match(service_id):
        raise DocsSourceCandidatesConfigError(f"Candidate #{index}: invalid service_id {service_id!r}.")
    if not ID_RE.match(docs_source):
        raise DocsSourceCandidatesConfigError(f"Candidate {service_id}: invalid docs_source {docs_source!r}.")
    if max_pages <= 0:
        raise DocsSourceCandidatesConfigError(f"Candidate {service_id}: max_pages must be > 0.")
    if crawl_depth < 0:
        raise DocsSourceCandidatesConfigError(f"Candidate {service_id}: crawl_depth must be >= 0.")
    if risk_level not in ALLOWED_RISK_LEVELS:
        raise DocsSourceCandidatesConfigError(f"Candidate {service_id}: invalid risk_level {risk_level!r}.")

    for url in official_start_urls:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise DocsSourceCandidatesConfigError(f"Candidate {service_id}: invalid official_start_url: {url}")
        host = parsed.hostname or ""
        if not _host_allowed(host, allowed_domains):
            raise DocsSourceCandidatesConfigError(
                f"Candidate {service_id}: official_start_url is outside allowed_domains: {url}"
            )
    for pattern in (*allow_patterns, *deny_patterns):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise DocsSourceCandidatesConfigError(
                f"Candidate {service_id}: invalid regex pattern {pattern!r}: {exc}"
            ) from exc

    return DocsSourceCandidate(
        service_id=service_id,
        display_name=display_name,
        aliases=tuple(dict.fromkeys(alias.strip() for alias in aliases if alias.strip())),
        docs_source=docs_source,
        official_start_urls=official_start_urls,
        allowed_domains=allowed_domains,
        allow_patterns=allow_patterns,
        deny_patterns=deny_patterns,
        max_pages=max_pages,
        crawl_depth=crawl_depth,
        risk_level=risk_level,  # type: ignore[arg-type]
        notes=notes,
    )


def _parse_candidates_yaml(text: str) -> list[dict[str, Any]]:
    """Parse the limited YAML subset used by config/docs_source_candidates.yaml."""
    candidates: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_list_key: str | None = None
    seen_candidates_key = False

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        raw_without_comment = raw_line.split("#", 1)[0].rstrip()
        if not raw_without_comment.strip():
            continue
        indent = len(raw_without_comment) - len(raw_without_comment.lstrip(" "))
        line = raw_without_comment.strip()
        if line == "candidates:":
            seen_candidates_key = True
            continue
        if not seen_candidates_key:
            raise DocsSourceCandidatesConfigError("Docs source candidates config must start with candidates:.")

        if line.startswith("- "):
            item = line[2:].strip()
            if indent == 2:
                if current is not None:
                    candidates.append(current)
                current = {}
                current_list_key = None
                if item:
                    key, value = _split_key_value(item, line_number)
                    current[key] = _scalar(value)
                continue
            if current is None or current_list_key is None:
                raise DocsSourceCandidatesConfigError(f"Line {line_number}: list item has no parent key.")
            current[current_list_key].append(_scalar(item))
            continue

        if current is None:
            raise DocsSourceCandidatesConfigError(f"Line {line_number}: candidate field appears before a candidate item.")
        key, value = _split_key_value(line, line_number)
        if value == "":
            current[key] = []
            current_list_key = key
        else:
            current[key] = _scalar(value)
            current_list_key = None

    if current is not None:
        candidates.append(current)
    return candidates


def _split_key_value(text: str, line_number: int) -> tuple[str, str]:
    if ":" not in text:
        raise DocsSourceCandidatesConfigError(f"Line {line_number}: expected key: value.")
    key, value = text.split(":", 1)
    key = key.strip()
    if not key:
        raise DocsSourceCandidatesConfigError(f"Line {line_number}: empty key.")
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
        raise DocsSourceCandidatesConfigError(f"Candidate #{index}: missing {key}.")
    return value


def _required_list(row: dict[str, Any], key: str, *, index: int) -> list[str]:
    value = row.get(key)
    if not isinstance(value, list) or not value:
        raise DocsSourceCandidatesConfigError(f"Candidate #{index}: {key} must be a non-empty list.")
    result = [str(item).strip() for item in value if str(item).strip()]
    if not result:
        raise DocsSourceCandidatesConfigError(f"Candidate #{index}: {key} must contain non-empty values.")
    return result


def _int(value: object, *, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise DocsSourceCandidatesConfigError(f"Expected integer value, got {value!r}.") from exc


def _normalize_domain(value: str, *, candidate: str) -> str:
    clean = value.strip().lower()
    if not clean or "/" in clean or ":" in clean:
        raise DocsSourceCandidatesConfigError(f"Candidate {candidate}: invalid allowed domain: {value}")
    return clean


def _host_allowed(host: str, domains: tuple[str, ...]) -> bool:
    clean = host.lower()
    return any(clean == domain or clean.endswith("." + domain) for domain in domains)
