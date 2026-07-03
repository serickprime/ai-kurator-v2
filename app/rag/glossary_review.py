"""Owner-reviewed glossary candidate apply planning.

This module never discovers candidates itself and never writes to Supabase. It
turns Phase 4A candidates into an owner-review file, validates owner decisions,
and builds a reviewed query glossary output only after explicit approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from app.rag.glossary_candidates import GlossaryCandidate, GlossaryCandidateReport
from app.rag.query_enrichment import (
    DEFAULT_QUERY_GLOSSARY_CONFIG,
    QueryGlossaryConfig,
    QueryGlossaryRule,
    QueryGlossaryService,
    load_query_glossary_config,
)

REVIEW_MODE = "owner-review-required"
REVIEW_SOURCE = "phase4a-glossary-candidates"
REVIEW_DECISIONS = {"pending", "approved", "rejected", "edited"}
DEFAULT_REVIEWED_GLOSSARY_OUTPUT = Path("reports/query_glossary.reviewed.yaml")


class GlossaryReviewError(ValueError):
    """Raised when a glossary review file or apply request is invalid."""


@dataclass(frozen=True)
class ReviewCandidate:
    """One candidate awaiting owner/admin review."""

    id: str
    service_id: str | None = None
    source_id: str | None = None
    topic: str = "general"
    user_phrases: tuple[str, ...] = ()
    technical_terms: tuple[str, ...] = ()
    exact_terms: tuple[str, ...] = ()
    config_terms: tuple[str, ...] = ()
    confidence: float = 0.0
    review_flags: tuple[str, ...] = ()
    current_status: str = "suggested"
    owner_decision: str = "pending"
    owner_notes: str = ""
    edited_terms: tuple[str, ...] = ()
    allow_sensitive_apply: bool = False

    @classmethod
    def from_candidate(cls, candidate: GlossaryCandidate, *, index: int) -> "ReviewCandidate":
        """Build a pending review item from a Phase 4A candidate."""
        return cls(
            id=f"candidate-{index:03d}",
            service_id=candidate.service_id,
            source_id=candidate.source_id,
            topic=candidate.topic,
            user_phrases=candidate.user_phrases,
            technical_terms=candidate.technical_terms,
            exact_terms=candidate.exact_terms,
            config_terms=candidate.config_terms,
            confidence=candidate.confidence,
            review_flags=candidate.review_flags,
            current_status=candidate.status,
        )

    @property
    def requires_sensitive_confirmation(self) -> bool:
        """Return true when this candidate must not apply without extra approval."""
        return self.current_status == "sensitive-review" or "sensitive-review" in self.review_flags


@dataclass(frozen=True)
class GlossaryReviewFile:
    """Owner-editable review file."""

    generated_at: str
    candidates: tuple[ReviewCandidate, ...]
    source: str = REVIEW_SOURCE
    mode: str = REVIEW_MODE
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApplyItem:
    """One reviewed glossary rule that would be added."""

    candidate_id: str
    service_id: str
    topic: str
    decision: str
    phrases: tuple[str, ...]
    exact_terms: tuple[str, ...]
    config_terms: tuple[str, ...]
    duplicate_terms_skipped: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApplyPlan:
    """Dry-run apply plan."""

    approved: int = 0
    edited: int = 0
    rejected: int = 0
    pending_skipped: int = 0
    sensitive_skipped: int = 0
    duplicate_terms_skipped: int = 0
    items: tuple[ApplyItem, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def has_changes(self) -> bool:
        """Return true when the plan has at least one rule to add."""
        return bool(self.items)


def review_file_from_report(
    report: GlossaryCandidateReport,
    *,
    generated_at: str | None = None,
) -> GlossaryReviewFile:
    """Convert a Phase 4A report into an owner-review file."""
    timestamp = generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    candidates = tuple(
        ReviewCandidate.from_candidate(candidate, index=index)
        for index, candidate in enumerate(report.candidates, start=1)
    )
    return GlossaryReviewFile(
        generated_at=timestamp,
        candidates=candidates,
        warnings=report.warnings,
    )


def dump_review_file(review: GlossaryReviewFile) -> str:
    """Serialize a review file in a small owner-editable YAML subset."""
    lines = [
        f"mode: {_quote(review.mode)}",
        f"generated_at: {_quote(review.generated_at)}",
        f"source: {_quote(review.source)}",
        *_field_list_lines("warnings", review.warnings, indent=0),
        "candidates:",
    ]
    if not review.candidates:
        lines[-1] = "candidates: []"
    for candidate in review.candidates:
        lines.extend(
            [
                f"  - id: {_quote(candidate.id)}",
                f"    service_id: {_quote(candidate.service_id or '')}",
                f"    source_id: {_quote(candidate.source_id or '')}",
                f"    topic: {_quote(candidate.topic)}",
                *_field_list_lines("user_phrases", candidate.user_phrases, indent=4),
                *_field_list_lines("technical_terms", candidate.technical_terms, indent=4),
                *_field_list_lines("exact_terms", candidate.exact_terms, indent=4),
                *_field_list_lines("config_terms", candidate.config_terms, indent=4),
                f"    confidence: {candidate.confidence:.2f}",
                *_field_list_lines("review_flags", candidate.review_flags, indent=4),
                f"    current_status: {_quote(candidate.current_status)}",
                f"    owner_decision: {_quote(candidate.owner_decision)}",
                f"    allow_sensitive_apply: {_bool_text(candidate.allow_sensitive_apply)}",
                f"    owner_notes: {_quote(candidate.owner_notes)}",
                *_field_list_lines("edited_terms", candidate.edited_terms, indent=4),
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def load_review_file(path: Path | str) -> GlossaryReviewFile:
    """Load a review file written by :func:`dump_review_file`."""
    return parse_review_file(Path(path).read_text(encoding="utf-8"))


def parse_review_file(text: str) -> GlossaryReviewFile:
    """Parse the small YAML subset used for glossary candidate reviews."""
    raw = _parse_review_yaml(text)
    mode = _as_str(raw.get("mode"))
    if mode != REVIEW_MODE:
        raise GlossaryReviewError(f"Review file mode must be {REVIEW_MODE!r}.")
    source = _as_str(raw.get("source")) or REVIEW_SOURCE
    generated_at = _as_str(raw.get("generated_at"))
    if not generated_at:
        raise GlossaryReviewError("Review file is missing generated_at.")
    candidates_raw = raw.get("candidates")
    if candidates_raw is None:
        candidates_raw = []
    if not isinstance(candidates_raw, list):
        raise GlossaryReviewError("Review file candidates must be a list.")
    candidates = tuple(_review_candidate_from_mapping(item, index=index + 1) for index, item in enumerate(candidates_raw))
    warnings = tuple(_as_list(raw.get("warnings")))
    return GlossaryReviewFile(
        generated_at=generated_at,
        source=source,
        candidates=candidates,
        warnings=warnings,
    )


def validate_review_file(review: GlossaryReviewFile) -> tuple[str, ...]:
    """Validate structure and owner decisions, returning non-fatal warnings."""
    warnings: list[str] = []
    seen_ids: set[str] = set()
    for candidate in review.candidates:
        if not candidate.id:
            raise GlossaryReviewError("Every candidate must have id.")
        if candidate.id in seen_ids:
            raise GlossaryReviewError(f"Duplicate candidate id: {candidate.id}")
        seen_ids.add(candidate.id)
        if candidate.owner_decision not in REVIEW_DECISIONS:
            raise GlossaryReviewError(
                f"Candidate {candidate.id}: owner_decision must be one of {', '.join(sorted(REVIEW_DECISIONS))}."
            )
        if candidate.owner_decision == "edited" and not candidate.edited_terms:
            raise GlossaryReviewError(f"Candidate {candidate.id}: edited decision requires edited_terms.")
        if candidate.requires_sensitive_confirmation and candidate.owner_decision in {"approved", "edited"} and not candidate.allow_sensitive_apply:
            warnings.append(f"Candidate {candidate.id}: sensitive-review will be skipped without allow_sensitive_apply: true.")
    return tuple(warnings)


def build_apply_plan(
    review: GlossaryReviewFile,
    existing_glossary: QueryGlossaryConfig,
) -> ApplyPlan:
    """Build a dry-run plan for approved or edited review candidates."""
    warnings = list(validate_review_file(review))
    existing_terms = _existing_terms(existing_glossary)
    planned_terms: set[str] = set()
    items: list[ApplyItem] = []
    rejected = 0
    pending_skipped = 0
    sensitive_skipped = 0
    duplicate_terms_skipped = 0
    approved_count = 0
    edited_count = 0

    for candidate in review.candidates:
        if candidate.owner_decision == "pending":
            pending_skipped += 1
            continue
        if candidate.owner_decision == "rejected":
            rejected += 1
            continue
        if candidate.requires_sensitive_confirmation and not candidate.allow_sensitive_apply:
            sensitive_skipped += 1
            continue

        terms = _candidate_terms(candidate)
        exact_terms, config_terms = _classify_apply_terms(terms, candidate=candidate)
        duplicate_terms: list[str] = []
        exact_terms, skipped_exact = _skip_duplicates(exact_terms, existing_terms=existing_terms, planned_terms=planned_terms)
        config_terms, skipped_config = _skip_duplicates(config_terms, existing_terms=existing_terms, planned_terms=planned_terms)
        duplicate_terms.extend(skipped_exact)
        duplicate_terms.extend(skipped_config)
        duplicate_terms_skipped += len(duplicate_terms)
        if not exact_terms and not config_terms:
            continue

        if candidate.owner_decision == "approved":
            approved_count += 1
        else:
            edited_count += 1
        service_id = _service_id_for_candidate(candidate)
        items.append(
            ApplyItem(
                candidate_id=candidate.id,
                service_id=service_id,
                topic=candidate.topic or "general",
                decision=candidate.owner_decision,
                phrases=_candidate_phrases(candidate),
                exact_terms=tuple(exact_terms),
                config_terms=tuple(config_terms),
                duplicate_terms_skipped=tuple(duplicate_terms),
            )
        )
        planned_terms.update(_term_key(term) for term in (*exact_terms, *config_terms))

    return ApplyPlan(
        approved=approved_count,
        edited=edited_count,
        rejected=rejected,
        pending_skipped=pending_skipped,
        sensitive_skipped=sensitive_skipped,
        duplicate_terms_skipped=duplicate_terms_skipped,
        items=tuple(items),
        warnings=tuple(warnings),
    )


def format_apply_plan(plan: ApplyPlan) -> str:
    """Format an owner-facing dry-run apply plan."""
    lines = [
        "Glossary Candidate Apply Plan",
        "",
        "- mode: dry-run",
        f"- approved: {plan.approved}",
        f"- edited: {plan.edited}",
        f"- rejected: {plan.rejected}",
        f"- pending skipped: {plan.pending_skipped}",
        f"- sensitive-review skipped: {plan.sensitive_skipped}",
        f"- duplicate terms skipped: {plan.duplicate_terms_skipped}",
        f"- changes: {len(plan.items)}",
        "- config write: none",
    ]
    for warning in plan.warnings:
        lines.append(f"- warning: {warning}")
    for error in plan.errors:
        lines.append(f"- error: {error}")
    for index, item in enumerate(plan.items, start=1):
        lines.extend(
            [
                "",
                f"Change {index}",
                "",
                f"- candidate: {item.candidate_id}",
                f"- service: {item.service_id}",
                f"- topic: {item.topic}",
                f"- decision: {item.decision}",
                "- phrases:",
                *_list_bullets(item.phrases),
                "- exact terms:",
                *_list_bullets(item.exact_terms),
                "- config terms:",
                *_list_bullets(item.config_terms),
                "- duplicates skipped:",
                *_list_bullets(item.duplicate_terms_skipped),
            ]
        )
    if not plan.items:
        lines.extend(["", "No reviewed changes would be applied."])
    return "\n".join(lines).rstrip() + "\n"


def reviewed_glossary_config(
    existing_glossary: QueryGlossaryConfig,
    plan: ApplyPlan,
) -> QueryGlossaryConfig:
    """Return a merged config with plan items appended as new rules."""
    if not plan.items:
        return existing_glossary
    services_by_id = {service.service_id: service for service in existing_glossary.services}
    rules_by_id = {service.service_id: list(service.rules) for service in existing_glossary.services}
    service_order = [service.service_id for service in existing_glossary.services]

    for item in plan.items:
        if item.service_id not in services_by_id:
            services_by_id[item.service_id] = QueryGlossaryService(
                service_id=item.service_id,
                display_name=_display_name(item.service_id),
                aliases=(item.service_id,),
                rules=(),
            )
            rules_by_id[item.service_id] = []
            service_order.append(item.service_id)
        rules_by_id[item.service_id].append(
            QueryGlossaryRule(
                phrases=item.phrases,
                exact_terms=item.exact_terms,
                config_terms=item.config_terms,
            )
        )

    services: list[QueryGlossaryService] = []
    for service_id in service_order:
        service = services_by_id[service_id]
        services.append(
            QueryGlossaryService(
                service_id=service.service_id,
                display_name=service.display_name,
                aliases=service.aliases,
                rules=tuple(rules_by_id[service_id]),
            )
        )
    return QueryGlossaryConfig(services=tuple(services))


def dump_query_glossary_config(config: QueryGlossaryConfig) -> str:
    """Serialize the query glossary config in the project's limited YAML style."""
    lines = [
        "# Reviewed query glossary output.",
        "# Generated from owner-approved glossary candidates.",
        "# Glossary entries are retrieval anchors only, not evidence or answers.",
        "",
    ]
    for service in config.services:
        lines.extend(
            [
                f"{service.service_id}:",
                f"  display_name: {_quote(service.display_name)}",
                *_field_list_lines("aliases", service.aliases, indent=2),
                "  rules:",
            ]
        )
        for rule in service.rules:
            lines.extend(
                [
                    "    - phrases:",
                    *_list_lines(rule.phrases, indent=8),
                    *_field_list_lines("exact_terms", rule.exact_terms, indent=6),
                    *_field_list_lines("config_terms", rule.config_terms, indent=6),
                ]
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def apply_reviewed_candidates(
    *,
    review: GlossaryReviewFile,
    config_path: Path | str = DEFAULT_QUERY_GLOSSARY_CONFIG,
    output_path: Path | str | None = DEFAULT_REVIEWED_GLOSSARY_OUTPUT,
    write_config: bool = False,
    confirm_reviewed_apply: bool = False,
) -> tuple[ApplyPlan, Path | None]:
    """Apply reviewed candidates to an output config file.

    Direct writes to ``config/query_glossary.yaml`` require both ``write_config``
    and ``confirm_reviewed_apply``. The default writes a reviewed copy under
    ``reports/`` and leaves the active config unchanged.
    """
    config_path = Path(config_path)
    existing = load_query_glossary_config(config_path)
    plan = build_apply_plan(review, existing)
    if write_config and not confirm_reviewed_apply:
        raise GlossaryReviewError("Direct config write requires --confirm-reviewed-apply.")
    if confirm_reviewed_apply and not write_config:
        raise GlossaryReviewError("--confirm-reviewed-apply is only valid together with --write-config.")
    if not plan.has_changes:
        return plan, None

    merged = reviewed_glossary_config(existing, plan)
    text = dump_query_glossary_config(merged)
    target = config_path if write_config else Path(output_path or DEFAULT_REVIEWED_GLOSSARY_OUTPUT)
    if not write_config:
        target = _safe_generated_output_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return plan, target


def _review_candidate_from_mapping(row: Any, *, index: int) -> ReviewCandidate:
    if not isinstance(row, Mapping):
        raise GlossaryReviewError(f"Candidate #{index} must be a mapping.")
    return ReviewCandidate(
        id=_required_str(row, "id", index=index),
        service_id=_optional_str(row.get("service_id")),
        source_id=_optional_str(row.get("source_id")),
        topic=_optional_str(row.get("topic")) or "general",
        user_phrases=tuple(_as_list(row.get("user_phrases"))),
        technical_terms=tuple(_as_list(row.get("technical_terms"))),
        exact_terms=tuple(_as_list(row.get("exact_terms"))),
        config_terms=tuple(_as_list(row.get("config_terms"))),
        confidence=_as_float(row.get("confidence")),
        review_flags=tuple(_as_list(row.get("review_flags"))),
        current_status=_optional_str(row.get("current_status")) or "suggested",
        owner_decision=_optional_str(row.get("owner_decision")) or "pending",
        owner_notes=_optional_str(row.get("owner_notes")) or "",
        edited_terms=tuple(_as_list(row.get("edited_terms"))),
        allow_sensitive_apply=_as_bool(row.get("allow_sensitive_apply")),
    )


def _parse_review_yaml(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_candidate: dict[str, Any] | None = None
    current_list_key: str | None = None

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if indent == 0:
            current_candidate = None
            current_list_key = None
            key, value = _split_key_value(line, line_number)
            if key == "candidates" and value == "":
                result[key] = []
            elif key == "warnings" and value == "":
                result[key] = []
                current_list_key = key
            else:
                result[key] = _parse_scalar_or_list(value)
            continue
        if indent == 2 and current_list_key == "warnings" and line.startswith("- "):
            result.setdefault("warnings", []).append(_parse_scalar(line[2:].strip()))
            continue
        if indent == 2 and line.startswith("- "):
            if "candidates" not in result or not isinstance(result["candidates"], list):
                raise GlossaryReviewError(f"Line {line_number}: candidate item appears before candidates:.")
            current_candidate = {}
            result["candidates"].append(current_candidate)
            item = line[2:].strip()
            if item:
                key, value = _split_key_value(item, line_number)
                current_candidate[key] = _parse_scalar_or_list(value)
                current_list_key = key if value == "" else None
            continue
        if current_candidate is None:
            raise GlossaryReviewError(f"Line {line_number}: expected candidate field.")
        if indent == 4:
            key, value = _split_key_value(line, line_number)
            current_candidate[key] = [] if value == "" else _parse_scalar_or_list(value)
            current_list_key = key if value == "" else None
            continue
        if indent == 6 and line.startswith("- ") and current_list_key:
            current_candidate.setdefault(current_list_key, []).append(_parse_scalar(line[2:].strip()))
            continue
        raise GlossaryReviewError(f"Line {line_number}: unsupported review file syntax.")
    return result


def _split_key_value(text: str, line_number: int) -> tuple[str, str]:
    if ":" not in text:
        raise GlossaryReviewError(f"Line {line_number}: expected key: value.")
    key, value = text.split(":", 1)
    key = key.strip()
    if not key:
        raise GlossaryReviewError(f"Line {line_number}: empty key.")
    return key, value.strip()


def _parse_scalar_or_list(value: str) -> object:
    if value == "[]":
        return []
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item.strip()) for item in inner.split(",")]
    return _parse_scalar(value)


def _parse_scalar(value: str) -> object:
    clean = value.strip()
    if clean.casefold() == "true":
        return True
    if clean.casefold() == "false":
        return False
    if len(clean) >= 2 and clean[:1] == clean[-1:] and clean[:1] in {"'", '"'}:
        return clean[1:-1].replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")
    try:
        return float(clean)
    except ValueError:
        return clean


def _required_str(row: Mapping[str, Any], key: str, *, index: int) -> str:
    value = _optional_str(row.get(key))
    if not value:
        raise GlossaryReviewError(f"Candidate #{index}: missing {key}.")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_str(value: object) -> str:
    return str(value or "").strip()


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() == "true"


def _as_float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _candidate_terms(candidate: ReviewCandidate) -> tuple[str, ...]:
    if candidate.owner_decision == "edited":
        return tuple(_dedupe(candidate.edited_terms, limit=40))
    return tuple(_dedupe([*candidate.exact_terms, *candidate.config_terms], limit=40))


def _classify_apply_terms(terms: Sequence[str], *, candidate: ReviewCandidate) -> tuple[list[str], list[str]]:
    if candidate.owner_decision != "edited":
        exact = _dedupe(candidate.exact_terms, limit=32)
        config = _dedupe(candidate.config_terms, limit=32)
        return exact, config

    exact: list[str] = []
    config: list[str] = []
    for term in terms:
        clean = _clean_term(term)
        if not clean:
            continue
        if _is_exact_apply_term(clean):
            exact.append(clean)
        else:
            config.append(clean)
    return _dedupe(exact, limit=32), _dedupe(config, limit=32)


def _candidate_phrases(candidate: ReviewCandidate) -> tuple[str, ...]:
    phrases = _dedupe(candidate.user_phrases, limit=12)
    if not phrases:
        phrases = _dedupe([candidate.topic], limit=1)
    return tuple(phrases)


def _service_id_for_candidate(candidate: ReviewCandidate) -> str:
    service_id = _clean_service_id(candidate.service_id or "")
    if service_id:
        return service_id
    source_id = _clean_service_id(candidate.source_id or "")
    if source_id.endswith("_docs"):
        return source_id[: -len("_docs")]
    return source_id or "reviewed_glossary"


def _skip_duplicates(
    terms: Sequence[str],
    *,
    existing_terms: set[str],
    planned_terms: set[str],
) -> tuple[list[str], list[str]]:
    kept: list[str] = []
    skipped: list[str] = []
    for term in terms:
        clean = _clean_term(term)
        key = _term_key(clean)
        if not clean:
            continue
        if key in existing_terms or key in planned_terms:
            skipped.append(clean)
            continue
        kept.append(clean)
    return _dedupe(kept, limit=32), _dedupe(skipped, limit=32)


def _existing_terms(config: QueryGlossaryConfig) -> set[str]:
    terms: set[str] = set()
    for service in config.services:
        for value in (service.service_id, service.display_name, *service.aliases):
            terms.add(_term_key(value))
        for rule in service.rules:
            for value in (*rule.phrases, *rule.exact_terms, *rule.config_terms):
                terms.add(_term_key(value))
    return {term for term in terms if term}


def _is_exact_apply_term(term: str) -> bool:
    return (
        term.startswith("/")
        or "(" in term
        or bool(re.search(r"\b[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*)*\s+node\b", term))
        or bool(re.search(r"[a-z][A-Z]", term))
    )


def _clean_term(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _term_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold().strip())


def _clean_service_id(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_").casefold()
    return clean


def _display_name(service_id: str) -> str:
    return " ".join(part.capitalize() for part in service_id.split("_") if part) or service_id


def _safe_generated_output_path(path: Path) -> Path:
    candidate = path if path.is_absolute() else Path.cwd() / path
    resolved = candidate.resolve()
    allowed_roots = ((Path.cwd() / "tmp").resolve(), (Path.cwd() / "reports").resolve())
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        raise GlossaryReviewError("Output path must be under tmp/ or reports/.")
    return resolved


def _quote(value: object) -> str:
    text = str(value or "")
    escaped = text.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _list_lines(items: Sequence[str], *, indent: int) -> list[str]:
    prefix = " " * indent
    if not items:
        return []
    return [f"{prefix}- {_quote(item)}" for item in items]


def _field_list_lines(key: str, items: Sequence[str], *, indent: int) -> list[str]:
    prefix = " " * indent
    if not items:
        return [f"{prefix}{key}: []"]
    return [f"{prefix}{key}:", *_list_lines(items, indent=indent + 2)]


def _list_bullets(items: Sequence[str]) -> list[str]:
    if not items:
        return ["  - none"]
    return [f"  - {item}" for item in items]


def _dedupe(items: Sequence[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = _clean_term(item)
        key = _term_key(clean)
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if len(result) >= limit:
            break
    return result
