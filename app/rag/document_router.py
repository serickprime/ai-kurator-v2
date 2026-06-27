"""Document router for document-first retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Protocol

from app.rag.types import DocumentCandidate, QuestionAnalysis, QueryFacet

_TOKEN_RE = re.compile(r"[\w#+.-]{2,}", re.UNICODE)
_PLATFORM_ROLES = {"platform"}
_SPECIFIC_ROLES = {"action", "object", "environment", "symptom", "constraint", "source"}


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


class SupabaseDocumentCardStore:
    """Supabase-backed document-card store."""

    def __init__(self, client: Any) -> None:
        self._client = client

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
                )
            )
        return records


class DocumentRouter:
    """Select a small set of answerable documents before evidence retrieval."""

    def __init__(
        self,
        store: DocumentCardStore | None = None,
        embedding_client: EmbeddingClient | None = None,
        min_score: float = 0.12,
    ) -> None:
        self._store = store
        self._embedding_client = embedding_client
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

        candidates = [
            _score_record(analysis, record)
            for record in records_by_id.values()
            if not _is_explicitly_not_about(analysis, record)
        ]
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


def _score_record(analysis: QuestionAnalysis, record: DocumentCardRecord) -> DocumentCandidate:
    platform_facets = [facet for facet in analysis.query_facets if facet.role in _PLATFORM_ROLES]
    specific_facets = [facet for facet in analysis.query_facets if facet.role in _SPECIFIC_ROLES]
    all_text = _record_text(record)
    answer_text = _record_answer_text(record)

    platform_matches = _matching_facets(platform_facets, all_text)
    specific_matches = _matching_facets(specific_facets, answer_text)
    matched_topics = _matched_items(record.topics, analysis)
    matched_questions = _matched_items(record.questions_answered, analysis)
    task_match = _task_matches(analysis.task_type, record.task_types)
    keyword_score = _keyword_score(analysis, answer_text)
    vector_score = record.vector_score or 0.0
    quality_score = max(min(record.quality_score or 0.0, 1.0), 0.0)

    platform_signal = sum(facet.importance for facet in platform_matches)
    specific_signal = sum(facet.importance for facet in specific_matches)
    question_signal = min(len(matched_questions) * 0.24, 0.48)
    topic_signal = min(len(matched_topics) * 0.14, 0.42)
    task_signal = 0.18 if task_match else 0.0

    score = (
        min(vector_score, 1.0) * 0.24
        + min(platform_signal, 1.5) * 0.08
        + min(specific_signal, 2.5) * 0.26
        + question_signal
        + topic_signal
        + task_signal
        + keyword_score * 0.18
        + quality_score * 0.04
    )

    if platform_signal and specific_signal < 0.3 and not matched_questions:
        score *= 0.35
    if not platform_signal and not specific_signal and vector_score < 0.68:
        score *= 0.45

    score = round(min(score, 1.0), 4)
    reason = _reason(
        vector_score=vector_score,
        platform_matches=platform_matches,
        specific_matches=specific_matches,
        matched_topics=matched_topics,
        matched_questions=matched_questions,
        task_match=task_match,
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


def _matched_items(items: tuple[str, ...], analysis: QuestionAnalysis) -> list[str]:
    needles = " ".join(
        [
            analysis.original_question,
            analysis.primary_intent,
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


def _is_explicitly_not_about(analysis: QuestionAnalysis, record: DocumentCardRecord) -> bool:
    if not record.not_about:
        return False
    query_roots = _roots(_tokens(_routing_query_text(analysis)))
    for item in record.not_about:
        if _roots(_tokens(item)) & query_roots:
            return True
    return False


def _reason(
    *,
    vector_score: float,
    platform_matches: list[QueryFacet],
    specific_matches: list[QueryFacet],
    matched_topics: list[str],
    matched_questions: list[str],
    task_match: bool,
) -> str:
    parts: list[str] = []
    if vector_score:
        parts.append(f"card embedding score {vector_score:.3f}")
    if specific_matches:
        parts.append("answerable facets: " + ", ".join(f"{facet.role}:{facet.text}" for facet in specific_matches[:5]))
    if matched_questions:
        parts.append("matched questions: " + "; ".join(matched_questions[:3]))
    if matched_topics:
        parts.append("matched topics: " + ", ".join(matched_topics[:4]))
    if task_match:
        parts.append("task type matches document card")
    if platform_matches and not specific_matches and not matched_questions:
        parts.append("platform/course match only; not enough by itself")
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
    return tuple(_normalize_token(token) for token in _TOKEN_RE.findall(text.lower()) if token.strip())


def _normalize_token(token: str) -> str:
    token = token.strip(".,:;!?()[]{}\"'`«»").lower()
    if token in {"н8н", "нейтн"}:
        return "n8n"
    return token


def _roots(tokens: tuple[str, ...] | list[str]) -> set[str]:
    return {_root(token) for token in tokens if token}


def _root(token: str) -> str:
    if len(token) >= 8:
        return token[:7]
    if len(token) >= 6:
        return token[:5]
    return token


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
