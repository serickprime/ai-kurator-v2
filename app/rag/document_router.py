"""Document router for document-first retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Protocol

from app.db.supabase_client import SupabaseRequestError
from app.rag import term_scoring
from app.rag.types import ContentType, DocumentCandidate, QuestionAnalysis, QueryFacet

_TOKEN_RE = re.compile(r"[\w#+.-]{2,}", re.UNICODE)
_PLATFORM_ROLES = {"platform"}
_SPECIFIC_ROLES = {"action", "object", "environment", "symptom", "constraint", "config", "exact", "rare_anchor", "source"}
_VALID_CONTENT_TYPES = {
    "lesson_material",
    "homework_task",
    "homework_review_rules",
    "course_catalog",
    "course_structure",
    "course_terms",
    "student_case",
    "official_docs",
    "external_docs",
    "platform_navigation",
    "personal_data",
    "unknown",
}
_CONTENT_TYPE_HINTS: dict[ContentType, tuple[str, ...]] = {
    "lesson_material": ("lesson", "material", "guide", "how_to", "setup", "explain", "урок", "материал"),
    "homework_task": ("homework", "assignment", "task", "deliverable", "дз", "домаш", "задани"),
    "homework_review_rules": ("review", "rubric", "criteria", "grading", "провер", "критер", "рубри"),
    "course_catalog": ("catalog", "available courses", "course list", "каталог", "список курсов"),
    "course_structure": ("structure", "module", "lesson list", "program", "структур", "модул", "программа"),
    "course_terms": ("terms", "deadline", "access", "price", "услов", "срок", "доступ", "тариф"),
    "student_case": ("case", "student", "debug", "support", "кейс", "разбор", "студент"),
    "official_docs": ("official", "docs", "documentation", "reference", "официаль", "документац"),
    "external_docs": ("external", "changelog", "release", "внешн", "релиз", "changelog"),
    "platform_navigation": ("screen", "menu", "button", "navigation", "экран", "меню", "кноп"),
    "personal_data": ("account", "personal", "user data", "личн", "аккаунт", "пользователь"),
}
_MATCH_STOPWORDS = {
    "and",
    "the",
    "for",
    "with",
    "from",
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
    "чем",
    "если",
    "или",
    "это",
    "этот",
    "эта",
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
    "не",
    "ни",
    "ли",
    "материал",
    "источник",
    "документ",
}


class EmbeddingClient(Protocol):
    """Embedding adapter used for optional card vector search."""

    async def embed(self, text: str) -> list[float]:
        """Embed one text string."""


class DocumentCardStore(Protocol):
    """Storage adapter for document-card routing."""

    async def list_document_cards(
        self,
        *,
        workspace_id: str,
        course: str | None,
        limit: int,
    ) -> list["DocumentCardRecord"]:
        """Return active document cards for lexical routing."""

    async def match_document_cards(
        self,
        *,
        workspace_id: str,
        query_embedding: list[float],
        course: str | None,
        limit: int,
    ) -> list["DocumentCardRecord"]:
        """Return active document cards from vector routing."""


@dataclass(frozen=True)
class DocumentCardRecord:
    """Document-card data needed by the router."""

    document_id: str
    filename: str
    title: str
    course: str | None = None
    lesson: str | None = None
    summary: str = ""
    topics: tuple[str, ...] = ()
    questions_answered: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    task_types: tuple[str, ...] = ()
    not_about: tuple[str, ...] = ()
    quality_score: float | None = None
    vector_score: float | None = None
    content_types: tuple[ContentType, ...] = ()
    metadata: dict[str, object] | None = None


class SupabaseDocumentCardStore:
    """Supabase-backed document-card store."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._term_statistics_available: bool | None = None

    async def list_document_cards(
        self,
        *,
        workspace_id: str,
        course: str | None,
        limit: int,
    ) -> list[DocumentCardRecord]:
        """Return active document cards and their document metadata."""
        documents = await self._client.select(
            "documents",
            params=_document_params(workspace_id=workspace_id, course=course, limit=limit),
        )
        return await self._cards_for_documents(documents)

    async def match_document_cards(
        self,
        *,
        workspace_id: str,
        query_embedding: list[float],
        course: str | None,
        limit: int,
    ) -> list[DocumentCardRecord]:
        """Search document cards by embedding, then hydrate card fields."""
        rows = await self._client.rpc(
            "match_document_cards",
            {
                "p_workspace_id": workspace_id,
                "p_query_embedding": query_embedding,
                "p_match_count": limit,
                "p_metadata_filter": {},
            },
        )
        if course:
            rows = [row for row in rows if row.get("course") == course]
        vector_scores = {str(row["document_id"]): _float_or_none(row.get("score")) for row in rows}
        documents = [
            {
                "id": str(row["document_id"]),
                "filename": row.get("filename") or "",
                "title": row.get("title") or "",
                "course": row.get("course"),
                "lesson": row.get("lesson"),
            }
            for row in rows
        ]
        cards = await self._cards_for_documents(documents)
        return [replace(card, vector_score=vector_scores.get(card.document_id)) for card in cards]

    async def list_term_statistics(
        self,
        *,
        workspace_id: str,
        course: str | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        """Return corpus term statistics for workspace-level rarity scoring."""
        del course
        if self._term_statistics_available is False:
            return []
        try:
            rows = await self._client.select(
                "term_statistics",
                params={
                    "select": (
                        "term,normalized_term,document_frequency,chunk_frequency,course_frequency,"
                        "first_seen_at,last_seen_at,examples,term_type_guess,metadata"
                    ),
                    "workspace_id": f"eq.{workspace_id}",
                    "order": "document_frequency.desc",
                    "limit": str(limit),
                },
            )
        except SupabaseRequestError as exc:
            if exc.is_missing_relation:
                self._term_statistics_available = False
                return []
            raise
        self._term_statistics_available = True
        return rows

    async def _cards_for_documents(self, documents: list[dict[str, Any]]) -> list[DocumentCardRecord]:
        if not documents:
            return []

        docs_by_id = {str(row["id"]): row for row in documents}
        ids_filter = ",".join(docs_by_id)
        cards = await self._client.select(
            "document_cards",
            params={
                "select": (
                    "document_id,summary,topics,questions_answered,entities,task_types,"
                    "not_about,quality_score,metadata"
                ),
                "document_id": f"in.({ids_filter})",
                "limit": str(len(docs_by_id)),
            },
        )
        records: list[DocumentCardRecord] = []
        for card in cards:
            document_id = str(card["document_id"])
            document = docs_by_id.get(document_id)
            if not document:
                continue
            records.append(
                DocumentCardRecord(
                    document_id=document_id,
                    filename=str(document.get("filename") or ""),
                    title=str(document.get("title") or ""),
                    course=_optional_str(document.get("course")),
                    lesson=_optional_str(document.get("lesson")),
                    summary=str(card.get("summary") or ""),
                    topics=_tuple_str(card.get("topics")),
                    questions_answered=_tuple_str(card.get("questions_answered")),
                    entities=_tuple_str(card.get("entities")),
                    task_types=_tuple_str(card.get("task_types")),
                    not_about=_tuple_str(card.get("not_about")),
                    quality_score=_float_or_none(card.get("quality_score")),
                    content_types=_content_types_from_card(card),
                    metadata=card.get("metadata") if isinstance(card.get("metadata"), dict) else {},
                )
            )
        return records


class DocumentRouter:
    """Select a small set of answerable documents before evidence retrieval."""

    def __init__(
        self,
        store: DocumentCardStore | None = None,
        embedding_client: EmbeddingClient | None = None,
        term_scorer: term_scoring.CorpusTermScorer | None = None,
        min_score: float = 0.12,
    ) -> None:
        self._store = store
        self._embedding_client = embedding_client
        self._term_scorer = term_scorer
        self._min_score = min_score

    async def route(
        self,
        analysis: QuestionAnalysis,
        workspace_id: str = "",
        course: str | None = None,
        limit: int = 5,
    ) -> tuple[DocumentCandidate, ...]:
        """Return candidate documents for a question analysis."""
        if self._store is None or not workspace_id:
            return ()

        pool_limit = max(limit * 12, 50)
        records_by_id: dict[str, DocumentCardRecord] = {}

        for record in await self._vector_candidates(analysis, workspace_id, course, pool_limit):
            records_by_id[record.document_id] = record

        for record in await self._store.list_document_cards(
            workspace_id=workspace_id,
            course=course,
            limit=pool_limit,
        ):
            existing = records_by_id.get(record.document_id)
            records_by_id[record.document_id] = _merge_records(existing, record)

        scorer = await self._corpus_scorer(workspace_id=workspace_id, course=course)
        candidates = [_score_record(analysis, record, scorer) for record in records_by_id.values()]
        candidates = [candidate for candidate in candidates if candidate.score >= self._min_score]
        candidates.sort(key=lambda item: (-item.score, item.title.lower(), item.document_id))
        return tuple(candidates[:limit])

    async def _vector_candidates(
        self,
        analysis: QuestionAnalysis,
        workspace_id: str,
        course: str | None,
        limit: int,
    ) -> list[DocumentCardRecord]:
        if self._embedding_client is None:
            return []

        try:
            query_embedding = await self._embedding_client.embed(_routing_query_text(analysis))
            return await self._store.match_document_cards(
                workspace_id=workspace_id,
                query_embedding=query_embedding,
                course=course,
                limit=limit,
            )
        except Exception:
            return []

    async def _corpus_scorer(self, *, workspace_id: str, course: str | None) -> term_scoring.CorpusTermScorer:
        if self._term_scorer is not None:
            return self._term_scorer
        if self._store is None or not hasattr(self._store, "list_term_statistics"):
            return term_scoring.CorpusTermScorer.neutral()
        try:
            rows = await getattr(self._store, "list_term_statistics")(
                workspace_id=workspace_id,
                course=course,
                limit=5000,
            )
        except Exception:
            return term_scoring.CorpusTermScorer.neutral()
        return term_scoring.CorpusTermScorer.from_rows(rows)


async def route_documents(
    question_analysis: QuestionAnalysis,
    workspace_id: str,
    course: str | None = None,
    limit: int = 5,
) -> list[DocumentCandidate]:
    """Route documents with the default Supabase and local embedding adapters."""
    from app.config import get_settings
    from app.db.supabase_client import SupabaseClient
    from app.llm.embeddings import OllamaEmbeddingClient

    settings = get_settings()
    embedding_client = OllamaEmbeddingClient(settings)
    async with SupabaseClient(settings) as supabase:
        try:
            router = DocumentRouter(
                store=SupabaseDocumentCardStore(supabase),
                embedding_client=embedding_client,
            )
            return list(
                await router.route(
                    question_analysis,
                    workspace_id=workspace_id,
                    course=course,
                    limit=limit,
                )
            )
        finally:
            await embedding_client.close()


def _score_record(
    analysis: QuestionAnalysis,
    record: DocumentCardRecord,
    scorer: term_scoring.CorpusTermScorer | None = None,
) -> DocumentCandidate:
    scorer = scorer or term_scoring.CorpusTermScorer.neutral()
    query_terms = scorer.query_terms(analysis)
    platform_facets = [facet for facet in analysis.query_facets if facet.role in _PLATFORM_ROLES]
    specific_facets = [facet for facet in analysis.query_facets if facet.role in _SPECIFIC_ROLES]
    all_text = _record_text(record)
    answer_text = _record_answer_text(record)
    object_terms = tuple(analysis.object_terms) or tuple(facet.text for facet in specific_facets if facet.role == "object")
    action_terms = tuple(_dedupe([analysis.requested_action, *[facet.text for facet in specific_facets if facet.role == "action"]], limit=4))
    constraint_terms = tuple(
        _dedupe(
            [
                analysis.requested_attribute,
                *analysis.constraints,
                *[facet.text for facet in specific_facets if facet.role in {"environment", "symptom", "constraint"}],
            ],
            limit=8,
        )
    )
    symptom_terms = tuple(
        _dedupe(
            [
                *analysis.symptom_terms,
                *[facet.text for facet in specific_facets if facet.role == "symptom"],
            ],
            limit=6,
        )
    )
    expected_content_types = tuple(content_type for content_type in analysis.expected_content_types if content_type != "unknown")
    record_content_types = record.content_types or _infer_content_types(record)

    platform_matches = _matching_facets(platform_facets, all_text)
    specific_matches = _matching_facets(specific_facets, answer_text)
    object_matches = _matching_terms(object_terms, answer_text)
    action_matches = _matching_terms(action_terms, answer_text)
    constraint_matches = _matching_terms(constraint_terms, answer_text)
    symptom_matches = _matching_terms(symptom_terms, answer_text)
    common_matches = list(
        scorer.matched_terms(
            tuple(query_terms.common_terms) + tuple(query_terms.platform_terms),
            all_text,
            role="common",
        )
    )
    anchor_terms = (
        tuple(query_terms.rare_anchor_terms)
        + tuple(query_terms.exact_terms)
        + tuple(query_terms.config_terms)
        + tuple(query_terms.symptom_terms)
    )
    anchor_matches = list(scorer.matched_terms(anchor_terms, answer_text, role="rare_anchor"))
    matched_topics = _matched_items(record.topics, analysis)
    matched_questions = _matched_items(record.questions_answered, analysis)
    task_match = _task_matches(analysis.task_type, record.task_types)
    keyword_score = _keyword_score(analysis, answer_text)
    vector_score = record.vector_score or 0.0
    quality_score = max(min(record.quality_score or 0.0, 1.0), 0.0)

    platform_signal = sum(facet.importance for facet in platform_matches)
    specific_signal = sum(facet.importance for facet in specific_matches)
    object_signal = scorer.weighted_match_ratio(object_terms, answer_text, role="object") if object_terms else 0.0
    action_signal = scorer.weighted_match_ratio(action_terms, answer_text, role="action") if action_terms else 0.0
    constraint_signal = (
        scorer.weighted_match_ratio(constraint_terms, answer_text, role="environment") if constraint_terms else 0.0
    )
    symptom_signal = scorer.weighted_match_ratio(symptom_terms, answer_text, role="symptom") if symptom_terms else 0.0
    common_signal = scorer.weighted_match_ratio(common_matches, all_text, role="common") if common_matches else 0.0
    anchor_signal = scorer.weighted_match_ratio(anchor_terms, answer_text, role="rare_anchor") if anchor_terms else 0.0
    content_type_signal = _content_type_signal(expected_content_types, record_content_types, record)
    course_hint_signal = _course_hint_signal(analysis, record)
    domain_hint_signal = _domain_hint_signal(analysis, record)
    action_object_signal = _action_object_signal(object_signal, action_signal, symptom_signal, matched_questions)
    not_about_signal = _not_about_signal(analysis, record)
    question_signal = min(len(matched_questions) * 0.20, 0.40)
    topic_signal = min(len(matched_topics) * 0.10, 0.30)
    task_signal = 0.12 if task_match else 0.0

    score = (
        min(vector_score, 1.0) * 0.16
        + min(platform_signal, 1.5) * 0.02
        + common_signal * 0.04
        + min(specific_signal, 2.5) * 0.12
        + anchor_signal * 0.28
        + object_signal * 0.34
        + action_signal * 0.16
        + constraint_signal * 0.16
        + symptom_signal * 0.18
        + content_type_signal * 0.16
        + min(course_hint_signal, 1.0) * 0.08
        + min(domain_hint_signal, 1.0) * 0.10
        + action_object_signal * 0.10
        + question_signal
        + topic_signal
        + task_signal
        + keyword_score * 0.10
        + quality_score * 0.03
    )

    penalties: list[str] = []
    if platform_signal and specific_signal < 0.3 and not matched_questions:
        score *= 0.35
        penalties.append("same_platform_but_wrong_task")
    if expected_content_types and record_content_types and content_type_signal == 0:
        score *= 0.58
        penalties.append("wrong_content_type_penalty")
    elif expected_content_types and not record_content_types and not (matched_questions or topic_signal):
        score *= 0.88
        penalties.append("unknown_content_type")
    if not_about_signal:
        score *= 0.18
        penalties.append("not_about_penalty")
    if common_matches and not (anchor_matches or object_matches or action_matches or constraint_matches or matched_questions):
        score *= 0.25
        penalties.append("general_common_term_only")
    if (course_hint_signal or domain_hint_signal or platform_signal) and not (
        anchor_matches
        or object_matches
        or action_matches
        or symptom_matches
        or constraint_matches
        or matched_questions
    ):
        score *= 0.42
        penalties.append("near_miss_penalty")
    if anchor_terms and not anchor_matches:
        score *= 0.78
        penalties.append("missing_anchor_terms")
    if object_terms and object_signal == 0:
        score *= 0.28
        penalties.append("missing_object_terms")
    elif len(object_terms) >= 3 and object_signal < 0.4:
        score *= 0.55
        penalties.append("weak_object_coverage")
    if action_terms and action_signal == 0 and not matched_questions:
        score *= 0.72
        penalties.append("missing_action_terms")
    if constraint_terms and constraint_signal == 0 and analysis.task_type in {"debug", "setup"}:
        score *= 0.82
        penalties.append("missing_constraint_terms")
    if not platform_signal and not specific_signal and vector_score < 0.68:
        score *= 0.45

    score_breakdown = {
        "vector": round(min(vector_score, 1.0), 4),
        "platform": round(min(platform_signal, 1.5), 4),
        "common_terms": round(common_signal, 4),
        "specific_facets": round(min(specific_signal, 2.5), 4),
        "rare_anchor_match": round(anchor_signal, 4),
        "object_match": round(object_signal, 4),
        "action_match": round(action_signal, 4),
        "symptom_match": round(symptom_signal, 4),
        "constraint_match": round(constraint_signal, 4),
        "content_type_match": round(content_type_signal, 4),
        "course_hint_bonus": round(course_hint_signal, 4),
        "domain_hint_bonus": round(domain_hint_signal, 4),
        "action_object_match": round(action_object_signal, 4),
        "question_match": round(question_signal, 4),
        "topic_match": round(topic_signal, 4),
        "task_type_match": round(task_signal, 4),
        "keyword_match": round(keyword_score, 4),
        "quality": round(quality_score, 4),
        "not_about": round(not_about_signal, 4),
    }
    score = round(score, 4)
    answerability_score = round(
        min(
            anchor_signal * 0.28
            + object_signal * 0.24
            + action_signal * 0.16
            + symptom_signal * 0.12
            + constraint_signal * 0.12
            + content_type_signal * 0.08,
            1.0,
        ),
        4,
    )
    reason = _reason(
        vector_score=vector_score,
        platform_matches=platform_matches,
        specific_matches=specific_matches,
        common_matches=common_matches,
        anchor_matches=anchor_matches,
        object_matches=object_matches,
        action_matches=action_matches,
        constraint_matches=constraint_matches,
        symptom_matches=symptom_matches,
        matched_topics=matched_topics,
        matched_questions=matched_questions,
        task_match=task_match,
        content_types=list(record_content_types),
        matched_content_types=_matched_content_types(expected_content_types, record_content_types),
        course_hint=analysis.course_hint,
        domain_hint=analysis.domain_hint,
        score_breakdown=score_breakdown,
        penalties=penalties,
    )
    route = "document_card_hybrid" if vector_score else "document_card_lexical"
    return DocumentCandidate(
        document_id=record.document_id,
        filename=record.filename,
        title=record.title,
        course=record.course,
        lesson=record.lesson,
        score=score,
        reason=reason,
        matched_topics=tuple(matched_topics),
        matched_questions=tuple(matched_questions),
        route=route,
        matched_common_terms=tuple(common_matches),
        matched_anchor_terms=tuple(anchor_matches),
        missing_action_terms=tuple(term for term in action_terms if term not in action_matches),
        missing_object_terms=tuple(term for term in object_terms if term not in object_matches),
        answerability_score=answerability_score,
        penalties=tuple(penalties),
        content_type=record_content_types[0] if record_content_types else "unknown",
        matched_content_types=tuple(_matched_content_types(expected_content_types, record_content_types)),
        score_breakdown=score_breakdown,
    )


def _routing_query_text(analysis: QuestionAnalysis) -> str:
    parts = [
        analysis.original_question,
        analysis.primary_intent,
        analysis.task_type,
        " ".join(facet.text for facet in analysis.query_facets),
        " ".join(analysis.must_answer_points),
        " ".join(analysis.evidence_questions),
    ]
    return "\n".join(part for part in parts if part)


def _record_text(record: DocumentCardRecord) -> str:
    return " ".join(
        [
            record.filename,
            record.title,
            record.course or "",
            record.lesson or "",
            record.summary,
            " ".join(record.topics),
            " ".join(record.questions_answered),
            " ".join(record.entities),
            " ".join(record.task_types),
            " ".join(record.content_types),
            _metadata_text(record.metadata),
        ]
    )


def _record_answer_text(record: DocumentCardRecord) -> str:
    return " ".join(
        [
            record.filename,
            record.title,
            record.lesson or "",
            record.summary,
            " ".join(record.topics),
            " ".join(record.questions_answered),
            " ".join(record.entities),
            " ".join(record.task_types),
            " ".join(record.content_types),
        ]
    )


def _matching_facets(facets: list[QueryFacet], text: str) -> list[QueryFacet]:
    text_roots = _roots(_tokens(text))
    matches: list[QueryFacet] = []
    for facet in facets:
        facet_roots = _roots(_tokens(facet.text))
        if facet_roots and facet_roots & text_roots:
            matches.append(facet)
    return matches


def _matching_terms(terms: tuple[str, ...], text: str) -> list[str]:
    text_roots = _roots(_tokens(text))
    matches: list[str] = []
    for term in terms:
        term_roots = _roots(_tokens(term))
        if term_roots and term_roots & text_roots:
            matches.append(term)
    return _dedupe(matches, limit=8)


def _matched_items(items: tuple[str, ...], analysis: QuestionAnalysis) -> list[str]:
    needles = " ".join(
        [
            analysis.original_question,
            analysis.primary_intent,
            analysis.requested_action,
            analysis.requested_attribute,
            " ".join(analysis.object_terms),
            " ".join(analysis.must_answer_points),
            " ".join(analysis.evidence_questions),
            " ".join(facet.text for facet in analysis.query_facets if facet.role != "platform"),
        ]
    )
    needle_roots = _roots(_tokens(needles))
    matched: list[str] = []
    for item in items:
        item_roots = _roots(_tokens(item))
        if item_roots & needle_roots:
            matched.append(item)
    return _dedupe(matched, limit=6)


def _keyword_score(analysis: QuestionAnalysis, text: str) -> float:
    keywords = [
        keyword
        for keyword in analysis.keywords
        if keyword and not any(facet.text == keyword and facet.role == "platform" for facet in analysis.query_facets)
        and keyword not in analysis.generic_terms
    ]
    if not keywords:
        return 0.0
    text_roots = _roots(_tokens(text))
    matched = sum(1 for keyword in keywords if _roots(_tokens(keyword)) & text_roots)
    return min(matched / max(len(keywords), 1), 1.0)


def _task_matches(task_type: str, task_types: tuple[str, ...]) -> bool:
    if task_type == "general":
        return False
    normalized = {_normalize_token(item) for item in task_types}
    return task_type in normalized or any(_root(task_type) == _root(item) for item in normalized)


def _content_types_from_card(card: dict[str, Any]) -> tuple[ContentType, ...]:
    metadata = card.get("metadata") if isinstance(card.get("metadata"), dict) else {}
    values: list[object] = []
    for key in ("content_type", "content_types", "source_priority", "material_type"):
        value = metadata.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif value:
            values.append(value)
    values.extend(_tuple_str(card.get("task_types")))
    parsed = [_normalize_content_type(value) for value in values]
    return tuple(content_type for content_type in _dedupe(parsed, limit=4) if content_type != "unknown")


def _infer_content_types(record: DocumentCardRecord) -> tuple[ContentType, ...]:
    explicit = tuple(content_type for content_type in record.content_types if content_type != "unknown")
    if explicit:
        return explicit

    text = _record_text(record).casefold()
    matches: list[ContentType] = []
    for content_type, hints in _CONTENT_TYPE_HINTS.items():
        if any(hint.casefold() in text for hint in hints):
            matches.append(content_type)
    return tuple(_dedupe(matches, limit=3))


def _normalize_content_type(value: object) -> ContentType:
    clean = re.sub(r"[\s-]+", "_", str(value or "").strip().casefold())
    aliases = {
        "lesson": "lesson_material",
        "material": "lesson_material",
        "how_to": "lesson_material",
        "homework": "homework_task",
        "assignment": "homework_task",
        "review_rules": "homework_review_rules",
        "rubric": "homework_review_rules",
        "catalog": "course_catalog",
        "structure": "course_structure",
        "terms": "course_terms",
        "case": "student_case",
        "docs": "official_docs",
        "documentation": "official_docs",
        "navigation": "platform_navigation",
        "personal": "personal_data",
    }
    normalized = aliases.get(clean, clean)
    if normalized in _VALID_CONTENT_TYPES:
        return normalized  # type: ignore[return-value]
    return "unknown"


def _matched_content_types(
    expected: tuple[ContentType, ...],
    actual: tuple[ContentType, ...],
) -> list[ContentType]:
    if not expected or not actual:
        return []
    return [content_type for content_type in actual if content_type in set(expected)]


def _content_type_signal(
    expected: tuple[ContentType, ...],
    actual: tuple[ContentType, ...],
    record: DocumentCardRecord,
) -> float:
    if not expected:
        return 0.0
    if actual:
        matched = _matched_content_types(expected, actual)
        if matched:
            return min(1.0, len(matched) / max(len(expected), 1) + 0.15)
        return 0.0

    text = _record_text(record).casefold()
    for expected_type in expected:
        if any(hint.casefold() in text for hint in _CONTENT_TYPE_HINTS.get(expected_type, ())):
            return 0.62
    return 0.0


def _course_hint_signal(analysis: QuestionAnalysis, record: DocumentCardRecord) -> float:
    hint = analysis.course_hint
    if not hint:
        return 0.0
    text = " ".join(
        [
            record.course or "",
            record.title,
            record.filename,
            _metadata_text(record.metadata),
        ]
    )
    hint_roots = _roots(_tokens(hint))
    if not hint_roots:
        return 0.0
    text_roots = _roots(_tokens(text))
    overlap = hint_roots & text_roots
    if not overlap:
        return 0.0
    coverage = len(overlap) / max(len(hint_roots), 1)
    return min(1.0, coverage * max(analysis.course_hint_confidence, 0.35))


def _domain_hint_signal(analysis: QuestionAnalysis, record: DocumentCardRecord) -> float:
    hint = analysis.domain_hint
    if not hint:
        return 0.0
    hint_roots = _roots(_tokens(hint))
    if not hint_roots:
        return 0.0
    text_roots = _roots(_tokens(_record_text(record)))
    if not (hint_roots & text_roots):
        return 0.0
    return max(0.25, min(1.0, analysis.domain_hint_confidence or 0.45))


def _action_object_signal(
    object_signal: float,
    action_signal: float,
    symptom_signal: float,
    matched_questions: list[str],
) -> float:
    if matched_questions:
        return 1.0
    signals = [value for value in (object_signal, action_signal, symptom_signal) if value > 0]
    if not signals:
        return 0.0
    return min(1.0, sum(signals) / max(len(signals), 1))


def _not_about_signal(analysis: QuestionAnalysis, record: DocumentCardRecord) -> float:
    if analysis.task_type == "compare":
        return 0.0
    if not record.not_about:
        return 0.0
    signal_terms = list(analysis.object_terms)
    signal_terms.extend(analysis.constraints)
    if analysis.requested_attribute:
        signal_terms.append(analysis.requested_attribute)
    signal_terms.extend(analysis.symptom_terms)
    query_roots = _roots(_tokens(" ".join(signal_terms)))
    if not query_roots:
        return 0.0
    for item in record.not_about:
        if _roots(_tokens(item)) & query_roots:
            return 1.0
    return 0.0


def _reason(
    *,
    vector_score: float,
    platform_matches: list[QueryFacet],
    specific_matches: list[QueryFacet],
    common_matches: list[str],
    anchor_matches: list[str],
    object_matches: list[str],
    action_matches: list[str],
    constraint_matches: list[str],
    symptom_matches: list[str],
    matched_topics: list[str],
    matched_questions: list[str],
    task_match: bool,
    content_types: list[ContentType],
    matched_content_types: list[ContentType],
    course_hint: str,
    domain_hint: str,
    score_breakdown: dict[str, float],
    penalties: list[str],
) -> str:
    parts: list[str] = []
    if vector_score:
        parts.append(f"card embedding score {vector_score:.3f}")
    if common_matches:
        parts.append("matched_common_terms: " + ", ".join(common_matches[:5]))
    if anchor_matches:
        parts.append("matched_anchor_terms: " + ", ".join(anchor_matches[:5]))
    if object_matches:
        parts.append("object terms: " + ", ".join(object_matches[:5]))
    if action_matches:
        parts.append("requested action: " + ", ".join(action_matches[:3]))
    if constraint_matches:
        parts.append("constraints: " + ", ".join(constraint_matches[:4]))
    if symptom_matches:
        parts.append("symptoms: " + ", ".join(symptom_matches[:4]))
    if matched_content_types:
        parts.append("content_type_match: " + ", ".join(matched_content_types))
    elif content_types:
        parts.append("content_type: " + ", ".join(content_types[:3]))
    if course_hint and score_breakdown.get("course_hint_bonus", 0) > 0:
        parts.append(f"soft course hint matched: {course_hint}")
    if domain_hint and score_breakdown.get("domain_hint_bonus", 0) > 0:
        parts.append(f"domain hint matched: {domain_hint}")
    if specific_matches:
        parts.append("answerable facets: " + ", ".join(f"{facet.role}:{facet.text}" for facet in specific_matches[:5]))
    if matched_questions:
        parts.append("matched questions: " + "; ".join(matched_questions[:3]))
    if matched_topics:
        parts.append("matched topics: " + ", ".join(matched_topics[:4]))
    if task_match:
        parts.append("task type matches document card")
    if penalties:
        parts.append("penalties: " + ", ".join(penalties[:5]))
    useful_breakdown = {
        key: value
        for key, value in score_breakdown.items()
        if value and key not in {"quality"}
    }
    if useful_breakdown:
        parts.append(
            "score_breakdown: "
            + ", ".join(f"{key}={value:.2f}" for key, value in list(useful_breakdown.items())[:8])
        )
    if platform_matches and not specific_matches and not matched_questions:
        parts.append("platform/course match only; not enough by itself")
    if common_matches and not (anchor_matches or object_matches or action_matches or constraint_matches or matched_questions):
        parts.append("general_common_term_only")
    return "; ".join(parts) or "low-confidence document-card match"


def _merge_records(
    existing: DocumentCardRecord | None,
    new: DocumentCardRecord,
) -> DocumentCardRecord:
    if existing is None:
        return new
    vector_score = existing.vector_score if existing.vector_score is not None else new.vector_score
    return replace(
        new,
        vector_score=vector_score,
        summary=new.summary or existing.summary,
        topics=new.topics or existing.topics,
        questions_answered=new.questions_answered or existing.questions_answered,
        entities=new.entities or existing.entities,
        task_types=new.task_types or existing.task_types,
        not_about=new.not_about or existing.not_about,
        quality_score=new.quality_score if new.quality_score is not None else existing.quality_score,
        content_types=new.content_types or existing.content_types,
        metadata=new.metadata or existing.metadata,
    )


def _document_params(workspace_id: str, course: str | None, limit: int) -> dict[str, str]:
    params = {
        "select": "id,filename,title,course,lesson",
        "workspace_id": f"eq.{workspace_id}",
        "status": "eq.active",
        "order": "updated_at.desc",
        "limit": str(limit),
    }
    if course:
        params["course"] = f"eq.{course}"
    return params


def _tuple_str(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value if str(item).strip())
    if isinstance(value, tuple):
        return tuple(str(item) for item in value if str(item).strip())
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return ()


def _metadata_text(metadata: dict[str, object] | None) -> str:
    if not metadata:
        return ""
    values: list[str] = []
    for key, value in metadata.items():
        if key.lower() in {"embedding", "raw_candidates", "discarded_candidates"}:
            continue
        if isinstance(value, (str, int, float, bool)):
            values.append(str(value))
        elif isinstance(value, list):
            values.extend(str(item) for item in value[:12] if isinstance(item, (str, int, float, bool)))
    return " ".join(values)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _tokens(text: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(text.lower()):
        normalized = _normalize_token(token)
        if not normalized or normalized in _MATCH_STOPWORDS:
            continue
        tokens.append(normalized)
    return tuple(tokens)


def _normalize_token(token: str) -> str:
    token = token.strip(".,:;!?()[]{}\"'`«»").lower()
    if token in {"н8н", "нейтн"}:
        return "n8n"
    return token


def _roots(tokens: tuple[str, ...] | list[str]) -> set[str]:
    return {_root(token) for token in tokens if token}


def _root(token: str) -> str:
    clean = _stem_ru(token)
    if len(clean) >= 8:
        return clean[:7]
    if len(clean) >= 6:
        return clean[:5]
    return clean


def _stem_ru(token: str) -> str:
    clean = token.casefold().replace("ё", "е").strip(".,:;!?()[]{}\"'`«»")
    if not re.search(r"[а-я]", clean):
        return clean
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
        if len(clean) > len(ending) + 3 and clean.endswith(ending):
            return clean[: -len(ending)]
    return clean


def _dedupe(items: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = re.sub(r"\s+", " ", item).strip()
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if len(result) >= limit:
            break
    return result
