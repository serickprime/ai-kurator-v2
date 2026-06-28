"""Evidence retrieval scoped to routed documents."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import Any, Protocol

from app.rag.types import DocumentCandidate, EvidenceSpan, QuestionAnalysis

LOGGER = logging.getLogger(__name__)
TOKEN_RE = re.compile(r"[\w#+.-]{2,}", re.UNICODE)
FACT_MARKER_RE = re.compile(r"\bFACT-ID:\s*[A-Z0-9_]+\b")

GENERIC_QUERY_TERMS = {
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
    "чем",
    "если",
    "или",
    "не",
    "ни",
    "ли",
    "это",
    "этот",
    "эта",
    "нужно",
    "нужен",
    "нужна",
    "нужны",
    "можно",
    "делать",
    "должен",
    "должна",
    "должно",
    "должны",
    "лучше",
    "для",
    "при",
    "после",
    "перед",
    "чтобы",
    "материал",
    "источник",
    "документ",
}


class EvidenceEmbeddingClient(Protocol):
    """Embedding adapter used by production evidence retrieval."""

    async def embed(self, text: str) -> list[float]:
        """Embed one query text."""


class EvidenceChunkStore(Protocol):
    """Store adapter for scoped chunk retrieval."""

    async def match_chunks(
        self,
        *,
        workspace_id: str,
        document_ids: tuple[str, ...],
        query_text: str,
        query_embedding: list[float] | None,
        match_count: int,
    ) -> list["EvidenceChunkRecord"]:
        """Return chunk candidates inside the selected documents."""


@dataclass(frozen=True)
class EvidenceChunkRecord:
    """Chunk candidate before final evidence selection."""

    chunk_id: str
    document_id: str
    content: str
    document_title: str = ""
    section_id: str | None = None
    heading: str | None = None
    page: int | None = None
    source_uri: str | None = None
    vector_score: float = 0.0
    text_score: float = 0.0
    trigram_score: float = 0.0
    score: float = 0.0
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class DiscardedEvidence:
    """Diagnostic record for candidates rejected before packing."""

    document_id: str
    chunk_id: str
    score: float
    reason: str
    preview: str


class SupabaseEvidenceChunkStore:
    """Supabase-backed chunk store using evidence-first RPCs."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def match_chunks(
        self,
        *,
        workspace_id: str,
        document_ids: tuple[str, ...],
        query_text: str,
        query_embedding: list[float] | None,
        match_count: int,
    ) -> list[EvidenceChunkRecord]:
        """Return chunk candidates scoped to selected documents."""
        if not workspace_id or not document_ids:
            return []

        payload = {
            "p_workspace_id": workspace_id,
            "p_document_ids": list(document_ids),
            "p_query_embedding": query_embedding,
            "p_query_text": query_text,
            "p_match_count": match_count,
        }
        try:
            rows = await self._client.rpc("hybrid_match_chunks_in_documents", payload)
        except Exception as exc:  # noqa: BLE001 - fallback keeps bot usable when RPC is missing
            LOGGER.warning("hybrid_match_chunks_in_documents failed; trying match_chunks_in_documents: %s", exc)
            try:
                rows = await self._client.rpc(
                    "match_chunks_in_documents",
                    {
                        key: value
                        for key, value in payload.items()
                        if key
                        in {
                            "p_workspace_id",
                            "p_document_ids",
                            "p_query_embedding",
                            "p_query_text",
                            "p_match_count",
                        }
                    },
                )
            except Exception as fallback_exc:  # noqa: BLE001
                LOGGER.warning("chunk RPC fallback failed; using scoped table scan: %s", fallback_exc)
                rows = await self._select_scoped_chunks(
                    workspace_id=workspace_id,
                    document_ids=document_ids,
                    match_count=match_count,
                )

        return [_record_from_row(row) for row in rows]

    async def _select_scoped_chunks(
        self,
        *,
        workspace_id: str,
        document_ids: tuple[str, ...],
        match_count: int,
    ) -> list[dict[str, Any]]:
        ids_filter = ",".join(document_ids)
        return await self._client.select(
            "chunks",
            params={
                "select": "id,document_id,section_id,content,page,heading,metadata,chunk_index",
                "workspace_id": f"eq.{workspace_id}",
                "document_id": f"in.({ids_filter})",
                "limit": str(max(match_count * 4, match_count)),
            },
        )


class EvidenceRetriever:
    """Retrieve evidence spans only inside selected documents."""

    def __init__(
        self,
        *,
        chunk_store: EvidenceChunkStore | None = None,
        client: Any | None = None,
        embedding_client: EvidenceEmbeddingClient | None = None,
        workspace_id: str = "",
        match_count: int = 32,
        max_evidence: int = 8,
        min_score: float = 0.18,
    ) -> None:
        self._chunk_store = chunk_store or (SupabaseEvidenceChunkStore(client) if client is not None else None)
        self._embedding_client = embedding_client
        self._workspace_id = workspace_id
        self._match_count = match_count
        self._max_evidence = max_evidence
        self._min_score = min_score
        self.last_discarded: tuple[DiscardedEvidence, ...] = ()

    async def retrieve(
        self,
        analysis: QuestionAnalysis,
        documents: tuple[DocumentCandidate, ...],
    ) -> tuple[EvidenceSpan, ...]:
        """Return evidence spans from the routed document set."""
        records = await self.candidate_records(analysis, documents)
        selected: list[EvidenceSpan] = []
        discarded: list[DiscardedEvidence] = []
        document_titles = _document_titles(documents)

        for record in records:
            scored = score_evidence_record(analysis, record)
            discard_reason = _discard_reason(analysis, scored, self._min_score)
            if discard_reason:
                discarded.append(_discarded(scored, discard_reason))
                continue

            selected.append(_span_from_record(scored, document_titles))
            if len(selected) >= self._max_evidence:
                break

        selected_ids = {span.evidence_id for span in selected}
        for record in records:
            if record.chunk_id in selected_ids or len(discarded) >= 16:
                continue
            scored = score_evidence_record(analysis, record)
            discard_reason = _discard_reason(analysis, scored, self._min_score) or "not selected after reranking"
            discarded.append(_discarded(scored, discard_reason))

        self.last_discarded = tuple(_dedupe_discarded(discarded, limit=16))
        return tuple(selected)

    async def candidate_records(
        self,
        analysis: QuestionAnalysis,
        documents: tuple[DocumentCandidate, ...],
    ) -> tuple[EvidenceChunkRecord, ...]:
        """Return scored chunk records before final evidence selection."""
        if self._chunk_store is None or not documents:
            self.last_discarded = ()
            return ()

        document_ids = _trusted_document_ids(analysis, documents)
        query_text = evidence_query_text(analysis)
        query_embedding = await self._query_embedding(query_text)
        try:
            records = await self._chunk_store.match_chunks(
                workspace_id=self._workspace_id,
                document_ids=document_ids,
                query_text=query_text,
                query_embedding=query_embedding,
                match_count=self._match_count,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("evidence chunk retrieval failed: %s", exc)
            return ()

        scored = [score_evidence_record(analysis, record) for record in records]
        scored.sort(key=lambda item: _sort_key(item))
        return tuple(scored)

    async def _query_embedding(self, query_text: str) -> list[float] | None:
        if self._embedding_client is None:
            return None
        try:
            return await self._embedding_client.embed(query_text)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("evidence query embedding failed; continuing with lexical retrieval: %s", exc)
            return None


def evidence_query_text(analysis: QuestionAnalysis) -> str:
    """Build compact search text for chunk retrieval."""
    parts = [
        analysis.original_question,
        analysis.primary_intent,
        analysis.requested_action,
        analysis.requested_attribute,
        " ".join(analysis.object_terms),
        " ".join(analysis.constraints),
        " ".join(facet.text for facet in analysis.query_facets if facet.role != "platform"),
        " ".join(analysis.evidence_questions),
    ]
    return "\n".join(part for part in parts if part)


def score_evidence_record(analysis: QuestionAnalysis, record: EvidenceChunkRecord) -> EvidenceChunkRecord:
    """Apply deterministic object-first scoring to one chunk candidate."""
    text = _record_text(record)
    text_roots = _roots(_tokens(text))
    query_roots = _query_roots(analysis)
    object_roots = _roots(analysis.object_terms)
    action_roots = _roots([analysis.requested_action]) if analysis.requested_action else set()
    constraint_roots = _constraint_roots(analysis)

    overlap = query_roots & text_roots
    object_overlap = object_roots & text_roots
    action_overlap = action_roots & text_roots
    constraint_overlap = constraint_roots & text_roots

    base = max(record.score, record.vector_score, record.text_score, record.trigram_score, 0.0)
    overlap_score = len(overlap) / max(len(query_roots), 1) if query_roots else 0.0
    object_score = len(object_overlap) / max(len(object_roots), 1) if object_roots else 0.0
    constraint_score = len(constraint_overlap) / max(len(constraint_roots), 1) if constraint_roots else 0.0
    action_score = 1.0 if action_overlap else 0.0
    fact_bonus = 0.16 if _has_fact_marker(record) else 0.0
    heading_bonus = 0.08 if _heading_supports_query(record, query_roots) else 0.0

    score = (
        base * 0.30
        + overlap_score * 0.32
        + object_score * 0.26
        + action_score * 0.08
        + constraint_score * 0.12
        + fact_bonus
        + heading_bonus
    )
    reason_parts: list[str] = []
    if overlap:
        reason_parts.append("matched roots: " + ", ".join(sorted(overlap)[:8]))
    if object_overlap:
        reason_parts.append("object match: " + ", ".join(sorted(object_overlap)[:5]))
    if action_overlap:
        reason_parts.append("action match")
    if constraint_overlap:
        reason_parts.append("constraint match: " + ", ".join(sorted(constraint_overlap)[:5]))
    if _has_fact_marker(record):
        reason_parts.append("supporting fact marker")

    metadata = dict(record.metadata or {})
    metadata.update(
        {
            "base_score": round(base, 4),
            "query_overlap": sorted(overlap),
            "object_overlap": sorted(object_overlap),
            "constraint_overlap": sorted(constraint_overlap),
            "retrieval_reason": "; ".join(reason_parts) or "low lexical support",
        }
    )
    return replace(record, score=round(min(score, 1.5), 4), metadata=metadata)


def _discard_reason(
    analysis: QuestionAnalysis,
    record: EvidenceChunkRecord,
    min_score: float,
) -> str:
    text = _record_text(record)
    text_roots = _roots(_tokens(text))
    object_roots = _roots(analysis.object_terms)
    constraint_roots = _constraint_roots(analysis)
    lowered_heading = (record.heading or "").casefold()

    if "не объясняет" in lowered_heading:
        return "not-about section"
    if object_roots and not (object_roots & text_roots):
        return "missing primary object terms"
    if constraint_roots and _requires_constraint(analysis) and not (constraint_roots & text_roots):
        return "missing requested constraint"
    if record.score < min_score:
        return "below evidence threshold"
    return ""


def _span_from_record(
    record: EvidenceChunkRecord,
    document_titles: dict[str, str],
) -> EvidenceSpan:
    locator_parts: list[str] = []
    if record.heading:
        locator_parts.append(record.heading)
    if record.page is not None:
        locator_parts.append(f"p. {record.page}")
    locator = ", ".join(locator_parts) or None
    metadata = dict(record.metadata or {})
    reason = str(metadata.get("retrieval_reason") or "")
    return EvidenceSpan(
        evidence_id=record.chunk_id,
        document_id=record.document_id,
        document_title=record.document_title or document_titles.get(record.document_id, record.document_id),
        text=record.content,
        locator=locator,
        source_uri=record.source_uri,
        score=record.score,
        is_source=True,
        retrieval_reason=reason,
        metadata=metadata,
    )


def _record_from_row(row: dict[str, Any]) -> EvidenceChunkRecord:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return EvidenceChunkRecord(
        chunk_id=str(row.get("chunk_id") or row.get("id") or ""),
        document_id=str(row.get("document_id") or ""),
        section_id=_optional_str(row.get("section_id")),
        content=str(row.get("content") or ""),
        heading=_optional_str(row.get("heading")),
        page=_int_or_none(row.get("page")),
        vector_score=_float(row.get("vector_score")),
        text_score=_float(row.get("text_score")),
        trigram_score=_float(row.get("trigram_score")),
        score=_float(row.get("score")),
        metadata=metadata,
    )


def _document_titles(documents: tuple[DocumentCandidate, ...]) -> dict[str, str]:
    titles: dict[str, str] = {}
    for document in documents:
        titles[document.document_id] = document.title or document.filename or document.document_id
        if document.filename:
            titles[document.filename] = document.title or document.filename
    return titles


def _trusted_document_ids(
    analysis: QuestionAnalysis,
    documents: tuple[DocumentCandidate, ...],
) -> tuple[str, ...]:
    if not documents:
        return ()
    if analysis.task_type in {"compare", "source_check"}:
        return tuple(_dedupe([document.document_id for document in documents[:3]], limit=3))
    return (documents[0].document_id,)


def _sort_key(record: EvidenceChunkRecord) -> tuple[float, str, str]:
    fact_bias = 0.02 if _has_fact_marker(record) else 0.0
    return (-(record.score + fact_bias), record.document_id, record.chunk_id)


def _record_text(record: EvidenceChunkRecord) -> str:
    return " ".join(
        [
            record.document_title,
            record.heading or "",
            record.content,
        ]
    )


def _query_roots(analysis: QuestionAnalysis) -> set[str]:
    parts = [
        analysis.original_question,
        analysis.primary_intent,
        analysis.requested_action,
        analysis.requested_attribute,
        " ".join(analysis.object_terms),
        " ".join(analysis.constraints),
    ]
    tokens = _tokens(" ".join(parts))
    generic = set(analysis.generic_terms) | GENERIC_QUERY_TERMS
    return _roots(token for token in tokens if token not in generic)


def _constraint_roots(analysis: QuestionAnalysis) -> set[str]:
    terms = list(analysis.constraints)
    terms.extend(facet.text for facet in analysis.query_facets if facet.role in {"environment", "symptom", "constraint"})
    if analysis.requested_attribute:
        terms.append(analysis.requested_attribute)
    return _roots(_tokens(" ".join(terms)))


def _requires_constraint(analysis: QuestionAnalysis) -> bool:
    return bool(analysis.constraints or analysis.task_type == "debug")


def _heading_supports_query(record: EvidenceChunkRecord, query_roots: set[str]) -> bool:
    if not record.heading or not query_roots:
        return False
    return bool(_roots(_tokens(record.heading)) & query_roots)


def _has_fact_marker(record: EvidenceChunkRecord) -> bool:
    if record.metadata and record.metadata.get("fact_ids"):
        return True
    return bool(FACT_MARKER_RE.search(record.content))


def _discarded(record: EvidenceChunkRecord, reason: str) -> DiscardedEvidence:
    return DiscardedEvidence(
        document_id=record.document_id,
        chunk_id=record.chunk_id,
        score=record.score,
        reason=reason,
        preview=re.sub(r"\s+", " ", record.content).strip()[:180],
    )


def _dedupe_discarded(items: list[DiscardedEvidence], limit: int) -> list[DiscardedEvidence]:
    seen: set[tuple[str, str, str]] = set()
    result: list[DiscardedEvidence] = []
    for item in items:
        key = (item.document_id, item.chunk_id, item.reason)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _tokens(text: str) -> list[str]:
    return [token.casefold().replace("ё", "е").strip(".,:;!?()[]{}\"'`«»") for token in TOKEN_RE.findall(text)]


def _roots(tokens: Any) -> set[str]:
    return {_root(str(token)) for token in tokens if str(token).strip()}


def _root(token: str) -> str:
    clean = token.casefold().replace("ё", "е").strip(".,:;!?()[]{}\"'`«»")
    clean = _stem_ru(clean)
    if len(clean) >= 8:
        return clean[:7]
    if len(clean) >= 6:
        return clean[:5]
    return clean


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


def _dedupe(items: list[str] | tuple[str, ...], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = str(item).strip()
        key = clean.casefold()
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if len(result) >= limit:
            break
    return result


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
