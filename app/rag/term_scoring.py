"""Corpus-aware term scoring for document-first retrieval.

The scorer deliberately avoids a fixed list of product/platform names. Terms
become weak or strong because of corpus frequency, query role, and code-like
shape, not because the code knows a particular vendor name.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from app.rag.types import QuestionAnalysis, QueryFacet

TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_#+./:-]{2,}", re.UNICODE)
CODE_LIKE_RE = re.compile(
    r"("
    r"[A-Za-zА-Яа-яЁё0-9_.-]+:[0-9]+"
    r"|[A-Za-zА-Яа-яЁё0-9_]+\([^\s)]*\)"
    r"|[A-Za-zА-Яа-яЁё0-9]+_[A-Za-zА-Яа-яЁё0-9_]+"
    r"|[A-Za-zА-Яа-яЁё]+[0-9]+[A-Za-zА-Яа-яЁё0-9_.-]*"
    r"|[A-ZА-ЯЁ0-9_]{4,}"
    r")",
    re.UNICODE,
)

TermFrequencyClass = Literal["common", "course_common", "medium", "specific", "rare_anchor", "unseen"]

STOPWORDS = {
    "and",
    "the",
    "for",
    "with",
    "from",
    "about",
    "into",
    "only",
    "как",
    "что",
    "где",
    "куда",
    "когда",
    "какой",
    "какая",
    "какие",
    "какого",
    "каком",
    "какую",
    "чем",
    "если",
    "или",
    "это",
    "этот",
    "эта",
    "эти",
    "для",
    "при",
    "после",
    "перед",
    "чтобы",
    "на",
    "из",
    "в",
    "во",
    "с",
    "со",
    "по",
    "про",
    "от",
    "до",
    "не",
    "ни",
    "ли",
    "нужно",
    "нужен",
    "нужна",
    "нужны",
    "можно",
    "делать",
    "сделать",
    "должен",
    "должна",
    "должно",
    "должны",
    "материал",
    "материале",
    "источник",
    "документ",
    "ответ",
    "вопрос",
    "пример",
}


@dataclass(frozen=True)
class TermStatistics:
    """Aggregated corpus frequency for one normalized term."""

    term: str
    document_frequency: int = 0
    chunk_frequency: int = 0
    course_frequency: int = 0
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    examples: tuple[str, ...] = ()
    term_type_guess: str = "term"
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def root(self) -> str:
        """Return the normalized matching root."""
        return root(self.term)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "TermStatistics":
        """Parse a Supabase row."""
        examples = row.get("examples") or ()
        if isinstance(examples, list):
            parsed_examples = tuple(_example_text(item) for item in examples if _example_text(item))
        else:
            parsed_examples = ()
        return cls(
            term=str(row.get("term") or row.get("normalized_term") or ""),
            document_frequency=_int(row.get("document_frequency")),
            chunk_frequency=_int(row.get("chunk_frequency")),
            course_frequency=_int(row.get("course_frequency")),
            first_seen_at=_optional_str(row.get("first_seen_at")),
            last_seen_at=_optional_str(row.get("last_seen_at")),
            examples=parsed_examples,
            term_type_guess=str(row.get("term_type_guess") or "term"),
            metadata=row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
        )


@dataclass(frozen=True)
class CorpusDocumentText:
    """Document text used to build local corpus statistics."""

    document_id: str
    text: str
    course: str | None = None
    chunks: tuple[str, ...] = ()
    title: str = ""


@dataclass(frozen=True)
class TermWeight:
    """One query term with corpus-aware weight."""

    term: str
    role: str
    frequency_class: TermFrequencyClass
    idf: float
    weight: float
    document_frequency: int = 0
    chunk_frequency: int = 0
    term_type_guess: str = "term"

    @property
    def is_weak(self) -> bool:
        """Return true when this term is common enough to avoid strong evidence."""
        return self.frequency_class in {"common", "course_common"} or self.weight < 0.45

    @property
    def is_anchor(self) -> bool:
        """Return true when this term can be a strong relevance anchor."""
        return self.frequency_class in {"specific", "rare_anchor"} or self.role in {
            "exact",
            "config",
            "symptom",
            "rare_anchor",
        }


@dataclass(frozen=True)
class QueryTermAnalysis:
    """Debug-friendly query term groups."""

    common_terms: tuple[str, ...] = ()
    platform_terms: tuple[str, ...] = ()
    action_terms: tuple[str, ...] = ()
    object_terms: tuple[str, ...] = ()
    symptom_terms: tuple[str, ...] = ()
    environment_terms: tuple[str, ...] = ()
    config_terms: tuple[str, ...] = ()
    exact_terms: tuple[str, ...] = ()
    rare_anchor_terms: tuple[str, ...] = ()
    ignored_weak_terms: tuple[str, ...] = ()
    strongest_evidence_terms: tuple[str, ...] = ()
    weights: dict[str, TermWeight] = field(default_factory=dict)


class CorpusTermScorer:
    """Score query terms by corpus frequency and query role."""

    def __init__(
        self,
        statistics: Sequence[TermStatistics] = (),
        *,
        total_documents: int | None = None,
        total_chunks: int | None = None,
        total_courses: int | None = None,
    ) -> None:
        self._by_root: dict[str, TermStatistics] = {}
        self.total_documents = max(total_documents or 0, 0)
        self.total_chunks = max(total_chunks or 0, 0)
        self.total_courses = max(total_courses or 0, 0)

        for stat in statistics:
            if not stat.term:
                continue
            existing = self._by_root.get(stat.root)
            merged = _merge_stats(existing, stat) if existing else stat
            self._by_root[stat.root] = merged
            self.total_documents = max(self.total_documents, merged.document_frequency)
            self.total_chunks = max(self.total_chunks, merged.chunk_frequency)
            self.total_courses = max(self.total_courses, merged.course_frequency)

        self.total_documents = max(self.total_documents, 1)
        self.total_chunks = max(self.total_chunks, 1)
        self.total_courses = max(self.total_courses, 1)

    @classmethod
    def neutral(cls) -> "CorpusTermScorer":
        """Return a scorer with no corpus knowledge."""
        return cls(())

    @classmethod
    def from_rows(cls, rows: Sequence[dict[str, Any]]) -> "CorpusTermScorer":
        """Build a scorer from `term_statistics` rows."""
        stats = tuple(TermStatistics.from_row(row) for row in rows)
        return cls(stats)

    @classmethod
    def from_documents(cls, documents: Sequence[CorpusDocumentText]) -> "CorpusTermScorer":
        """Build corpus statistics from local documents."""
        return cls(build_term_statistics_from_documents(documents), total_documents=len(documents))

    @property
    def has_statistics(self) -> bool:
        """Return true when corpus statistics are available."""
        return bool(self._by_root)

    def term_weight(self, term: str, role: str = "object") -> TermWeight:
        """Return the corpus-aware weight for one term."""
        clean = normalize_token(term)
        if not clean:
            return TermWeight(term=term, role=role, frequency_class="unseen", idf=1.0, weight=0.0)

        stat = self._by_root.get(root(clean))
        if stat is None:
            term_type = guess_term_type(clean)
            base_weight = 1.05 if term_type != "term" else 0.82
            role_weight = _role_multiplier(role)
            return TermWeight(
                term=clean,
                role=role,
                frequency_class="unseen",
                idf=1.0,
                weight=round(min(base_weight * role_weight, 1.35), 4),
                term_type_guess=term_type,
            )

        doc_ratio = stat.document_frequency / max(self.total_documents, 1)
        chunk_ratio = stat.chunk_frequency / max(self.total_chunks, 1)
        idf = math.log((self.total_documents + 1) / (stat.document_frequency + 1)) + 1.0
        frequency_class = _frequency_class(stat, doc_ratio, chunk_ratio)
        base_weight = {
            "common": 0.24,
            "course_common": 0.38,
            "medium": 0.72,
            "specific": 1.05,
            "rare_anchor": 1.28,
            "unseen": 0.82,
        }[frequency_class]

        pattern_bonus = 0.16 if stat.term_type_guess != "term" else 0.0
        role_weight = _role_multiplier(role)
        weight = (base_weight + pattern_bonus) * role_weight
        if frequency_class in {"common", "course_common"} and role in {"common", "platform"}:
            weight = min(weight, 0.38)
        return TermWeight(
            term=clean,
            role=role,
            frequency_class=frequency_class,
            idf=round(idf, 4),
            weight=round(min(weight, 1.6), 4),
            document_frequency=stat.document_frequency,
            chunk_frequency=stat.chunk_frequency,
            term_type_guess=stat.term_type_guess,
        )

    def query_terms(self, analysis: QuestionAnalysis) -> QueryTermAnalysis:
        """Classify question terms into debug-friendly weighted groups."""
        weights: dict[str, TermWeight] = {}
        grouped: dict[str, list[str]] = defaultdict(list)

        for term, role in _analysis_terms(analysis):
            weight = self.term_weight(term, role=role)
            if not weight.term or weight.term in STOPWORDS:
                continue
            weights[f"{role}:{weight.term}"] = weight

            if weight.frequency_class in {"common", "course_common"}:
                grouped["common_terms"].append(weight.term)
                grouped["ignored_weak_terms"].append(weight.term)
            if role == "platform":
                grouped["platform_terms"].append(weight.term)
            elif role == "action":
                grouped["action_terms"].append(weight.term)
            elif role == "object":
                grouped["object_terms"].append(weight.term)
            elif role == "symptom":
                grouped["symptom_terms"].append(weight.term)
            elif role == "environment":
                grouped["environment_terms"].append(weight.term)
            elif role == "config":
                grouped["config_terms"].append(weight.term)
            elif role == "exact":
                grouped["exact_terms"].append(weight.term)
            if weight.is_anchor:
                grouped["rare_anchor_terms"].append(weight.term)
            if weight.weight >= 0.85 and role not in {"platform", "common", "source"}:
                grouped["strongest_evidence_terms"].append(weight.term)

        return QueryTermAnalysis(
            common_terms=tuple(dedupe(grouped["common_terms"], limit=16)),
            platform_terms=tuple(dedupe(grouped["platform_terms"], limit=16)),
            action_terms=tuple(dedupe(grouped["action_terms"], limit=16)),
            object_terms=tuple(dedupe(grouped["object_terms"], limit=16)),
            symptom_terms=tuple(dedupe(grouped["symptom_terms"], limit=16)),
            environment_terms=tuple(dedupe(grouped["environment_terms"], limit=16)),
            config_terms=tuple(dedupe(grouped["config_terms"], limit=16)),
            exact_terms=tuple(dedupe(grouped["exact_terms"], limit=16)),
            rare_anchor_terms=tuple(dedupe(grouped["rare_anchor_terms"], limit=16)),
            ignored_weak_terms=tuple(dedupe(grouped["ignored_weak_terms"], limit=16)),
            strongest_evidence_terms=tuple(dedupe(grouped["strongest_evidence_terms"], limit=16)),
            weights=weights,
        )

    def weighted_match_ratio(self, terms: Sequence[str], text: str, role: str = "object") -> float:
        """Return matched weighted coverage for terms in text."""
        if not terms:
            return 0.0
        text_roots = roots(tokens(text))
        total = 0.0
        matched = 0.0
        for term in terms:
            weight = self.term_weight(term, role=role).weight
            term_roots = roots(tokens(term))
            if not term_roots:
                continue
            total += max(weight, 0.05)
            if term_roots & text_roots:
                matched += max(weight, 0.05)
        return min(matched / max(total, 0.01), 1.0)

    def matched_terms(self, terms: Sequence[str], text: str, role: str = "object") -> tuple[str, ...]:
        """Return terms that appear in text after normalization."""
        text_roots = roots(tokens(text))
        matches = [
            term
            for term in terms
            if roots(tokens(term)) & text_roots and self.term_weight(term, role=role).weight > 0
        ]
        return tuple(dedupe(matches, limit=16))

    def has_strong_evidence_match(self, analysis: QuestionAnalysis, text: str) -> bool:
        """Return true when text matches at least one non-common evidence term."""
        term_analysis = self.query_terms(analysis)
        strong_terms = tuple(
            dedupe(
                [
                    *term_analysis.strongest_evidence_terms,
                    *term_analysis.rare_anchor_terms,
                    *term_analysis.exact_terms,
                    *term_analysis.config_terms,
                    *term_analysis.symptom_terms,
                ],
                limit=24,
            )
        )
        if not strong_terms:
            strong_terms = tuple(analysis.object_terms) + tuple(analysis.constraints)
        if not strong_terms:
            return True
        return bool(self.matched_terms(strong_terms, text, role="rare_anchor"))


def build_term_statistics_from_documents(documents: Sequence[CorpusDocumentText]) -> tuple[TermStatistics, ...]:
    """Build term statistics from local texts for tests and eval."""
    doc_frequency: Counter[str] = Counter()
    chunk_frequency: Counter[str] = Counter()
    courses: dict[str, set[str]] = defaultdict(set)
    examples: dict[str, list[str]] = defaultdict(list)

    for document in documents:
        document_terms = set(significant_terms(" ".join([document.title, document.text])))
        for term in document_terms:
            doc_frequency[term] += 1
            if document.course:
                courses[term].add(document.course)
            if len(examples[term]) < 3:
                examples[term].append(document.title or document.document_id)

        chunks = document.chunks or (document.text,)
        for chunk in chunks:
            for term in set(significant_terms(chunk)):
                chunk_frequency[term] += 1

    stats = [
        TermStatistics(
            term=term,
            document_frequency=count,
            chunk_frequency=chunk_frequency.get(term, 0),
            course_frequency=len(courses.get(term) or set()),
            examples=tuple(examples.get(term) or ()),
            term_type_guess=guess_term_type(term),
        )
        for term, count in doc_frequency.items()
    ]
    return tuple(sorted(stats, key=lambda item: (item.term, item.document_frequency)))


def significant_terms(text: str) -> tuple[str, ...]:
    """Extract normalized corpus terms, excluding language stopwords."""
    return tuple(
        dedupe(
            [
                token
                for token in (normalize_token(match) for match in TOKEN_RE.findall(text or ""))
                if token and token not in STOPWORDS and not token.isdigit()
            ],
            limit=None,
        )
    )


def exact_terms(text: str) -> tuple[str, ...]:
    """Extract code-like exact terms from query text."""
    terms = [normalize_token(match.group(0)) for match in CODE_LIKE_RE.finditer(text or "")]
    return tuple(dedupe([term for term in terms if term and term not in STOPWORDS], limit=12))


def guess_term_type(term: str) -> str:
    """Guess term type from shape, not from vendor dictionaries."""
    clean = term.strip()
    if not clean:
        return "term"
    if re.search(r"[A-Za-zА-Яа-яЁё0-9_.-]+:[0-9]+", clean):
        return "endpoint_or_address"
    if re.search(r"[A-Za-zА-Яа-яЁё0-9_]+\([^\s)]*\)", clean):
        return "function"
    if "_" in clean:
        return "identifier"
    if "=" in clean or clean.startswith("--"):
        return "config"
    if re.search(r"[A-Za-zА-Яа-яЁё]+[0-9]+|[0-9]+[A-Za-zА-Яа-яЁё]+", clean):
        return "technical_identifier"
    if "." in clean and not clean.endswith(".md"):
        return "path_or_parameter"
    if re.fullmatch(r"[A-ZА-ЯЁ0-9_]{4,}", clean):
        return "error_or_code"
    return "term"


def normalize_token(token: str) -> str:
    """Normalize one token for matching."""
    clean = str(token).casefold().replace("ё", "е").strip(".,:;!?()[]{}\"'`«»")
    if clean in {"н8н", "нейтн"}:
        return "n8n"
    return clean


def tokens(text: str) -> tuple[str, ...]:
    """Tokenize text for matching."""
    return tuple(normalize_token(match) for match in TOKEN_RE.findall(text or "") if normalize_token(match))


def roots(items: Iterable[str]) -> set[str]:
    """Return normalized roots for terms."""
    return {root(item) for item in items if str(item).strip()}


def root(token: str) -> str:
    """Return a lightweight language-agnostic/Russian root."""
    clean = normalize_token(token)
    clean = _stem_ru(clean)
    if len(clean) >= 8:
        return clean[:7]
    if len(clean) >= 6:
        return clean[:5]
    return clean


def dedupe(items: Sequence[str], limit: int | None = None) -> list[str]:
    """Preserve order while removing duplicates."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = re.sub(r"\s+", " ", str(item)).strip()
        key = clean.casefold()
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if limit is not None and len(result) >= limit:
            break
    return result


def _analysis_terms(analysis: QuestionAnalysis) -> list[tuple[str, str]]:
    terms: list[tuple[str, str]] = []
    for facet in analysis.query_facets:
        role = _role_for_facet(facet)
        terms.append((facet.text, role))
    terms.extend((term, "object") for term in analysis.object_terms)
    terms.extend((term, "common") for term in analysis.generic_terms)
    terms.extend((term, "action") for term in [analysis.requested_action] if term)
    terms.extend((term, "environment") for term in analysis.constraints)
    terms.extend((term, "exact") for term in getattr(analysis, "exact_terms", ()))
    terms.extend((term, "config") for term in getattr(analysis, "config_terms", ()))
    terms.extend((term, "symptom") for term in getattr(analysis, "symptom_terms", ()))
    return [(normalize_token(term), role) for term, role in terms if normalize_token(term)]


def _role_for_facet(facet: QueryFacet) -> str:
    if facet.role in {"platform", "action", "object", "environment", "symptom", "constraint", "source"}:
        return "environment" if facet.role == "constraint" else facet.role
    return str(facet.role)


def _frequency_class(stat: TermStatistics, doc_ratio: float, chunk_ratio: float) -> TermFrequencyClass:
    if doc_ratio >= 0.35 or chunk_ratio >= 0.30:
        return "common"
    if doc_ratio >= 0.18 and stat.course_frequency <= 1:
        return "course_common"
    if stat.document_frequency <= 1 or doc_ratio <= 0.04:
        return "rare_anchor"
    if doc_ratio <= 0.12:
        return "specific"
    return "medium"


def _role_multiplier(role: str) -> float:
    if role in {"common", "platform", "source"}:
        return 0.72
    if role in {"exact", "config", "symptom", "rare_anchor"}:
        return 1.24
    if role == "action":
        return 1.08
    if role in {"environment", "constraint"}:
        return 0.96
    return 1.0


def _merge_stats(existing: TermStatistics | None, new: TermStatistics) -> TermStatistics:
    if existing is None:
        return new
    return TermStatistics(
        term=existing.term if len(existing.term) <= len(new.term) else new.term,
        document_frequency=max(existing.document_frequency, new.document_frequency),
        chunk_frequency=max(existing.chunk_frequency, new.chunk_frequency),
        course_frequency=max(existing.course_frequency, new.course_frequency),
        first_seen_at=existing.first_seen_at or new.first_seen_at,
        last_seen_at=new.last_seen_at or existing.last_seen_at,
        examples=tuple(dedupe([*existing.examples, *new.examples], limit=3)),
        term_type_guess=existing.term_type_guess if existing.term_type_guess != "term" else new.term_type_guess,
        metadata={**existing.metadata, **new.metadata},
    )


def _stem_ru(token: str) -> str:
    if not re.search(r"[а-я]", token):
        return token
    endings = (
        "иями",
        "ями",
        "ами",
        "ого",
        "ему",
        "ыми",
        "ими",
        "его",
        "ая",
        "яя",
        "ое",
        "ее",
        "ые",
        "ие",
        "ый",
        "ий",
        "ой",
        "ом",
        "ем",
        "ах",
        "ях",
        "ов",
        "ев",
        "ам",
        "ям",
        "ою",
        "ею",
        "ей",
        "у",
        "ю",
        "а",
        "я",
        "ы",
        "и",
        "е",
        "ь",
    )
    for ending in endings:
        if len(token) > len(ending) + 3 and token.endswith(ending):
            return token[: -len(ending)]
    return token


def _example_text(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("title") or value.get("filename") or value.get("document_id") or "").strip()
    return str(value).strip()


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: object) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0
