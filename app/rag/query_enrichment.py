"""Deterministic retrieval query enrichment from a curated glossary."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
import unicodedata
from typing import Any, Sequence

from app.rag.types import GlossaryDerivedAnchor, QueryEnrichmentContext, QueryFacet

DEFAULT_QUERY_GLOSSARY_CONFIG = Path("config/query_glossary.yaml")
SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class QueryGlossaryConfigError(ValueError):
    """Raised when the query glossary config is invalid."""


@dataclass(frozen=True)
class QueryGlossaryRule:
    """One natural-language-to-technical-anchor rule."""

    phrases: tuple[str, ...]
    exact_terms: tuple[str, ...] = ()
    config_terms: tuple[str, ...] = ()
    object_anchors: tuple[str, ...] = ()
    rule_id: str = ""


@dataclass(frozen=True)
class QueryGlossaryService:
    """Rules for one service in the query glossary."""

    service_id: str
    display_name: str
    aliases: tuple[str, ...]
    rules: tuple[QueryGlossaryRule, ...]


@dataclass(frozen=True)
class QueryGlossaryConfig:
    """Loaded query glossary config."""

    services: tuple[QueryGlossaryService, ...]


@dataclass(frozen=True)
class QueryEnrichment:
    """Retrieval-only enrichment produced from a user question."""

    normalized_question: str = ""
    normalized_user_terms: tuple[str, ...] = ()
    service_ids: tuple[str, ...] = ()
    exact_terms: tuple[str, ...] = ()
    config_terms: tuple[str, ...] = ()
    object_anchors: tuple[GlossaryDerivedAnchor, ...] = ()
    facets: tuple[QueryFacet, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Return true when no enrichment matched."""
        return not (self.service_ids or self.exact_terms or self.config_terms or self.object_anchors or self.facets)

    @property
    def context(self) -> QueryEnrichmentContext:
        """Return this legacy enrichment as a typed context."""
        return QueryEnrichmentContext(
            normalized_question=self.normalized_question,
            normalized_user_terms=self.normalized_user_terms,
            confirmed_service_ids=self.service_ids,
            glossary_object_anchors=self.object_anchors,
            exact_terms=self.exact_terms,
            config_terms=self.config_terms,
            facets=self.facets,
        )


GlossaryObjectAnchor = GlossaryDerivedAnchor


class QueryEnricher:
    """Apply query glossary rules without changing the original question."""

    def __init__(self, config: QueryGlossaryConfig | None = None) -> None:
        self._config = config or QueryGlossaryConfig(services=())

    @classmethod
    def empty(cls) -> "QueryEnricher":
        """Return an enricher that never adds anchors."""
        return cls(QueryGlossaryConfig(services=()))

    @classmethod
    def from_config(
        cls,
        path: Path | str = DEFAULT_QUERY_GLOSSARY_CONFIG,
        *,
        strict: bool = False,
    ) -> "QueryEnricher":
        """Load a query glossary. Missing or invalid configs safely become empty by default."""
        try:
            return cls(load_query_glossary_config(path))
        except QueryGlossaryConfigError:
            if strict:
                raise
            return cls.empty()

    @classmethod
    def default(cls) -> "QueryEnricher":
        """Return the cached default query enricher."""
        return _default_query_enricher()

    def enrich(self, question: str) -> QueryEnrichment:
        """Return technical retrieval anchors for a user question."""
        context = self.build_context(question)
        return QueryEnrichment(
            normalized_question=context.normalized_question,
            normalized_user_terms=context.normalized_user_terms,
            service_ids=context.confirmed_service_ids,
            exact_terms=context.exact_terms,
            config_terms=context.config_terms,
            object_anchors=context.glossary_object_anchors,
            facets=context.facets,
        )

    def build_context(self, question: str) -> QueryEnrichmentContext:
        """Return typed enrichment context for one user question."""
        normalized = _normalize_text(question)
        if not normalized:
            return QueryEnrichmentContext()

        service_ids: list[str] = []
        exact_terms: list[str] = []
        config_terms: list[str] = []
        object_anchors: list[GlossaryDerivedAnchor] = []
        facets: list[QueryFacet] = []

        for service in self._config.services:
            if not _service_matches(service, normalized):
                continue
            matched = False
            for rule in service.rules:
                matched_variant = _matched_rule_variant(rule, normalized)
                if matched_variant is None:
                    continue
                matched = True
                exact_terms.extend(rule.exact_terms)
                exact_terms.extend(rule.object_anchors)
                config_terms.extend(rule.config_terms)
                facets.extend(QueryFacet("exact", term, 1.0) for term in rule.exact_terms)
                facets.extend(QueryFacet("exact", term, 1.0) for term in rule.object_anchors)
                facets.extend(QueryFacet("config", term, 1.0) for term in rule.config_terms)
                object_anchors.extend(
                    GlossaryDerivedAnchor(
                        service_id=service.service_id,
                        term=term,
                        matched_variant=matched_variant,
                        rule_id=rule.rule_id,
                    )
                    for term in rule.object_anchors
                )
            if matched:
                service_ids.append(service.service_id)
                facets.insert(0, QueryFacet("platform", service.display_name, 1.0))

        return QueryEnrichmentContext(
            normalized_question=normalized,
            normalized_user_terms=tuple(_normalized_user_terms(normalized, limit=48)),
            confirmed_service_ids=tuple(_dedupe(service_ids, limit=12)),
            exact_terms=tuple(_dedupe(exact_terms, limit=24)),
            config_terms=tuple(_dedupe(config_terms, limit=24)),
            glossary_object_anchors=tuple(_dedupe_object_anchors(object_anchors, limit=24)),
            facets=tuple(_dedupe_facets(facets, limit=64)),
        )


@lru_cache(maxsize=1)
def _default_query_enricher() -> QueryEnricher:
    return QueryEnricher.from_config(DEFAULT_QUERY_GLOSSARY_CONFIG)


def load_query_glossary_config(path: Path | str = DEFAULT_QUERY_GLOSSARY_CONFIG) -> QueryGlossaryConfig:
    """Load and validate query enrichment glossary config."""
    config_path = Path(path)
    if not config_path.exists():
        raise QueryGlossaryConfigError(f"Query glossary config not found: {config_path}")
    rows = _parse_query_glossary_yaml(config_path.read_text(encoding="utf-8"))
    services = tuple(_service_from_mapping(row, index=index + 1) for index, row in enumerate(rows))
    if not services:
        raise QueryGlossaryConfigError("Query glossary config must contain at least one service.")

    service_ids = [service.service_id for service in services]
    if len(service_ids) != len(set(service_ids)):
        raise QueryGlossaryConfigError("Query glossary config contains duplicate service ids.")
    return QueryGlossaryConfig(services=services)


def _service_from_mapping(row: dict[str, Any], *, index: int) -> QueryGlossaryService:
    service_id = _required_str(row, "service_id", index=index).casefold()
    if not SERVICE_ID_RE.match(service_id):
        raise QueryGlossaryConfigError(f"Service #{index}: invalid service_id {service_id!r}.")
    display_name = _optional_str(row.get("display_name")) or _display_name(service_id)
    aliases = tuple(_required_list(row, "aliases", index=index))
    rules_raw = row.get("rules")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise QueryGlossaryConfigError(f"Service {service_id}: rules must be a non-empty list.")
    rules = tuple(_rule_from_mapping(item, service_id=service_id, index=rule_index + 1) for rule_index, item in enumerate(rules_raw))
    return QueryGlossaryService(
        service_id=service_id,
        display_name=display_name,
        aliases=tuple(_dedupe(aliases, limit=32)),
        rules=rules,
    )


def _rule_from_mapping(row: Any, *, service_id: str, index: int) -> QueryGlossaryRule:
    if not isinstance(row, dict):
        raise QueryGlossaryConfigError(f"Service {service_id}: rule #{index} must be a mapping.")
    phrases = tuple(_required_list(row, "phrases", index=index))
    exact_terms = tuple(_optional_list(row.get("exact_terms")))
    config_terms = tuple(_optional_list(row.get("config_terms")))
    object_anchors = tuple(_optional_list(row.get("object_anchors")))
    if not exact_terms and not config_terms and not object_anchors:
        raise QueryGlossaryConfigError(
            f"Service {service_id}: rule #{index} must define exact_terms, config_terms, or object_anchors."
        )
    return QueryGlossaryRule(
        phrases=tuple(_dedupe(phrases, limit=32)),
        exact_terms=tuple(_dedupe(exact_terms, limit=32)),
        config_terms=tuple(_dedupe(config_terms, limit=32)),
        object_anchors=tuple(_dedupe(object_anchors, limit=32)),
        rule_id=f"{service_id}:rule:{index}",
    )


def _parse_query_glossary_yaml(text: str) -> list[dict[str, Any]]:
    """Parse the limited YAML subset used by config/query_glossary.yaml."""
    services: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_field: str | None = None
    current_rule: dict[str, Any] | None = None
    current_rule_field: str | None = None

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        raw_without_comment = raw_line.split("#", 1)[0].rstrip()
        if not raw_without_comment.strip():
            continue
        indent = len(raw_without_comment) - len(raw_without_comment.lstrip(" "))
        line = raw_without_comment.strip()

        if indent == 0:
            if line.startswith("- ") or not line.endswith(":"):
                raise QueryGlossaryConfigError(f"Line {line_number}: expected service_id:.")
            if current is not None:
                services.append(current)
            service_id = line[:-1].strip()
            current = {"service_id": service_id}
            current_field = None
            current_rule = None
            current_rule_field = None
            continue

        if current is None:
            raise QueryGlossaryConfigError(f"Line {line_number}: field appears before a service.")

        if indent == 2:
            key, value = _split_key_value(line, line_number)
            if value == "":
                current[key] = []
                current_field = key
            else:
                current[key] = _scalar(value)
                current_field = None
            current_rule = None
            current_rule_field = None
            continue

        if current_field == "aliases" and indent == 4 and line.startswith("- "):
            current[current_field].append(_scalar(line[2:].strip()))
            continue

        if current_field == "rules":
            if indent == 4 and line.startswith("- "):
                item = line[2:].strip()
                current_rule = {}
                current[current_field].append(current_rule)
                current_rule_field = None
                if item:
                    key, value = _split_key_value(item, line_number)
                    current_rule[key] = [] if value == "" else _scalar(value)
                    current_rule_field = key if value == "" else None
                continue
            if current_rule is None:
                raise QueryGlossaryConfigError(f"Line {line_number}: rule field appears before a rule item.")
            if indent == 6:
                key, value = _split_key_value(line, line_number)
                current_rule[key] = [] if value == "" else _scalar(value)
                current_rule_field = key if value == "" else None
                continue
            if indent == 8 and line.startswith("- ") and current_rule_field:
                current_rule[current_rule_field].append(_scalar(line[2:].strip()))
                continue

        raise QueryGlossaryConfigError(f"Line {line_number}: unsupported query glossary syntax.")

    if current is not None:
        services.append(current)
    return services


def _split_key_value(text: str, line_number: int) -> tuple[str, str]:
    if ":" not in text:
        raise QueryGlossaryConfigError(f"Line {line_number}: expected key: value.")
    key, value = text.split(":", 1)
    key = key.strip()
    if not key:
        raise QueryGlossaryConfigError(f"Line {line_number}: empty key.")
    return key, value.strip()


def _scalar(value: str) -> object:
    clean = value.strip()
    if len(clean) >= 2 and clean[:1] == clean[-1:] and clean[:1] in {"'", '"'}:
        clean = clean[1:-1]
    clean = clean.replace("\\\\", "\\")
    return clean


def _required_str(row: dict[str, Any], key: str, *, index: int) -> str:
    value = str(row.get(key) or "").strip()
    if not value:
        raise QueryGlossaryConfigError(f"Service #{index}: missing {key}.")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_list(row: dict[str, Any], key: str, *, index: int) -> list[str]:
    value = row.get(key)
    if not isinstance(value, list) or not value:
        raise QueryGlossaryConfigError(f"Rule/service #{index}: {key} must be a non-empty list.")
    result = [str(item).strip() for item in value if str(item).strip()]
    if not result:
        raise QueryGlossaryConfigError(f"Rule/service #{index}: {key} must contain non-empty values.")
    return result


def _optional_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _service_matches(service: QueryGlossaryService, normalized: str) -> bool:
    return any(_normalize_text(alias) in normalized for alias in service.aliases)


def _matched_rule_variant(rule: QueryGlossaryRule, normalized: str) -> str | None:
    candidates = (*rule.phrases, *rule.exact_terms, *rule.object_anchors)
    for candidate in candidates:
        if candidate and normalize_glossary_text(candidate) in normalized:
            return candidate
    return None


def normalize_glossary_text(text: str) -> str:
    """Normalize text for glossary and evidence matching without changing source text."""
    clean = str(text or "").casefold().replace("ё", "е")
    clean = clean.replace("н8н", "n8n").replace("нейтн", "n8n")
    clean = _normalize_glossary_dashes(clean)
    return re.sub(r"\s+", " ", clean).strip()


def _normalize_text(text: str) -> str:
    return normalize_glossary_text(text)


def _normalize_glossary_dashes(text: str) -> str:
    normalized: list[str] = []
    in_dash_run = False
    for char in text:
        if _glossary_dash_char(char):
            if not in_dash_run:
                normalized.append(" ")
            in_dash_run = True
            continue
        normalized.append(char)
        in_dash_run = False
    return "".join(normalized)


def _glossary_dash_char(char: str) -> bool:
    return bool(char) and (char == "\u2212" or unicodedata.category(char) == "Pd")


def _display_name(service_id: str) -> str:
    return " ".join(part.capitalize() for part in service_id.split("_"))


def _dedupe(items: Sequence[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = re.sub(r"\s+", " ", str(item)).strip()
        key = clean.casefold()
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if len(result) >= limit:
            break
    return result


def _dedupe_facets(facets: Sequence[QueryFacet], limit: int) -> list[QueryFacet]:
    seen: set[tuple[str, str]] = set()
    result: list[QueryFacet] = []
    for facet in facets:
        key = (facet.role, facet.text.casefold())
        if key in seen:
            continue
        seen.add(key)
        result.append(facet)
        if len(result) >= limit:
            break
    return result


def _dedupe_object_anchors(
    anchors: Sequence[GlossaryDerivedAnchor],
    limit: int,
) -> list[GlossaryDerivedAnchor]:
    seen: set[tuple[str, str]] = set()
    result: list[GlossaryDerivedAnchor] = []
    for anchor in anchors:
        key = (anchor.service_id.casefold(), normalize_glossary_text(anchor.term))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        result.append(anchor)
        if len(result) >= limit:
            break
    return result


def _normalized_user_terms(normalized: str, *, limit: int) -> list[str]:
    return _dedupe(re.findall(r"[\w#+./:]{2,}", normalized, flags=re.UNICODE), limit=limit)
