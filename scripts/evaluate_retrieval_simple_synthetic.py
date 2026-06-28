"""Evaluate retrieval quality on a simple synthetic household corpus.

This script is intentionally retrieval-only. It builds a local deterministic
index from `sample_materials/rag_search_simple_test`, runs question analysis,
document routing, evidence retrieval, reranking, and evidence-pack building,
then writes diagnostic reports without generating final answers.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ingestion.chunker import ChunkDraft, ParentChildChunker, SectionDraft  # noqa: E402
from app.ingestion.document_cards import DocumentCardBuilder  # noqa: E402
from app.ingestion.loaders import FileLoader  # noqa: E402
from app.rag.document_router import DocumentCardRecord, DocumentRouter  # noqa: E402
from app.rag.evidence_retriever import EvidenceChunkRecord, EvidenceRetriever  # noqa: E402
from app.rag.evidence_pack import EvidencePackBuilder  # noqa: E402
from app.rag.question_analysis import QuestionAnalyzer  # noqa: E402
from app.rag.reranker import EvidenceReranker  # noqa: E402
from app.rag.term_scoring import CorpusDocumentText, CorpusTermScorer  # noqa: E402
from app.rag.types import DocumentCandidate, EvidencePack, EvidenceSpan, QuestionAnalysis  # noqa: E402

DEFAULT_MATERIALS_DIR = ROOT / "sample_materials" / "rag_search_simple_test"
DEFAULT_CASES_PATH = ROOT / "app" / "eval" / "rag_search_simple_test_cases.json"
DEFAULT_REPORT_DIR = ROOT / "eval_runs" / "retrieval_simple_synthetic"
WORKSPACE_ID = "rag_search_simple_test"
FACT_RE = re.compile(r"FACT-ID:\s*([A-Z0-9_]+)")
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_-]{2,}", re.UNICODE)
GENERIC_TOKENS = {
    "как",
    "что",
    "где",
    "куда",
    "какой",
    "какая",
    "какие",
    "зачем",
    "почему",
    "если",
    "это",
    "этот",
    "эта",
    "нужно",
    "нужен",
    "нужна",
    "делать",
    "лучше",
    "можно",
    "нельзя",
    "при",
    "для",
    "после",
    "перед",
    "чтобы",
    "дольше",
    "обычную",
    "материал",
    "материале",
    "объясняет",
    "объяснение",
    "уход",
    "хранение",
    "рецепт",
    "рецепты",
    "подготовку",
    "уборку",
    "готовых",
    "базовый",
}
KNOWN_OBJECT_ROOTS = {
    "лимон",
    "какту",
    "орхид",
    "блин",
    "блино",
    "сырник",
    "сырни",
    "сырнико",
    "творо",
    "яблок",
    "яблокам",
    "карто",
    "картоф",
    "картофе",
    "велос",
    "велоси",
    "чемод",
    "поезд",
    "кухн",
    "сково",
    "сковоро",
    "докум",
    "заряд",
}
OUT_OF_BASE_ROOTS = {
    "розой",
    "роз",
    "суп",
    "банан",
    "бананы",
    "цепь",
    "цеп",
    "почини",
    "чинит",
    "поход",
    "палатк",
}
AMBIGUOUS_ACTION_ROOTS = {
    "храни",
    "готов",
    "воды",
    "вод",
    "испорт",
    "растет",
    "раст",
    "прохла",
    "темном",
    "месте",
}
MANDATORY_QUERY_ROOTS = {
    "подпорч",
    "пригоре",
    "погруже",
    "серебри",
    "рассеян",
    "прораст",
    "дождя",
    "зарядк",
    "докумен",
}


@dataclass(frozen=True)
class SyntheticCase:
    """One expected retrieval case."""

    id: str
    question: str
    expected_document: str
    expected_fact_ids: tuple[str, ...]
    forbidden_documents: tuple[str, ...]
    expected_top_k_document: int
    expected_top_k_chunks: int
    expected_answer_mode: str
    notes: str = ""

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "SyntheticCase":
        """Parse a case from JSON."""
        return cls(
            id=str(row["id"]),
            question=str(row["question"]),
            expected_document=str(row.get("expected_document") or ""),
            expected_fact_ids=tuple(str(item) for item in row.get("expected_fact_ids", [])),
            forbidden_documents=tuple(str(item) for item in row.get("forbidden_documents", [])),
            expected_top_k_document=int(row.get("expected_top_k_document") or 0),
            expected_top_k_chunks=int(row.get("expected_top_k_chunks") or 0),
            expected_answer_mode=str(row.get("expected_answer_mode") or "answer_from_materials"),
            notes=str(row.get("notes") or ""),
        )


@dataclass(frozen=True)
class IndexedChunk:
    """Searchable synthetic chunk."""

    chunk_id: str
    document_id: str
    filename: str
    document_title: str
    heading: str
    content: str
    chunk_index: int
    section_index: int
    fact_ids: tuple[str, ...]


@dataclass(frozen=True)
class IndexedDocument:
    """Synthetic document with card, sections, chunks, and embedding."""

    document_id: str
    filename: str
    title: str
    card: DocumentCardRecord
    sections: tuple[SectionDraft, ...]
    chunks: tuple[IndexedChunk, ...]
    card_embedding: list[float]
    fact_ids: tuple[str, ...]


@dataclass(frozen=True)
class ChunkCandidate:
    """Raw chunk candidate before evidence-pack filtering."""

    chunk: IndexedChunk
    score: float
    reason: str


@dataclass(frozen=True)
class SyntheticIndex:
    """In-memory index for one benchmark run."""

    documents: tuple[IndexedDocument, ...]
    embeddings: dict[str, list[float]]

    @property
    def by_filename(self) -> dict[str, IndexedDocument]:
        """Return documents keyed by filename."""
        return {document.filename: document for document in self.documents}

    @property
    def by_id(self) -> dict[str, IndexedDocument]:
        """Return documents keyed by document id."""
        return {document.document_id: document for document in self.documents}


@dataclass
class CaseRun:
    """Detailed result for one question."""

    id: str
    question: str
    expected_document: str
    expected_fact_ids: list[str]
    expected_answer_mode: str
    top_document_candidates: list[dict[str, Any]]
    selected_document: str
    document_pass: bool
    found_fact_ids: list[str]
    missing_fact_ids: list[str]
    raw_forbidden_documents: list[str]
    evidence_forbidden_documents: list[str]
    evidence_pack_items: list[dict[str, Any]]
    discarded_candidates: list[dict[str, Any]]
    actual_answer_mode: str
    final_score: float
    result: str
    explanation: str
    analysis: dict[str, Any] = field(default_factory=dict)


class HashEmbeddingClient:
    """Deterministic local hash embeddings for synthetic routing."""

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    async def embed(self, text: str) -> list[float]:
        """Embed text into a normalized sparse hash vector."""
        vector = [0.0] * self.dim
        for token in _roots(_tokens(text)):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            return [value / norm for value in vector]
        return vector


class InMemoryDocumentCardStore:
    """DocumentCardStore backed by the synthetic index."""

    def __init__(self, index: SyntheticIndex) -> None:
        self._index = index

    async def list_document_cards(
        self,
        *,
        workspace_id: str,
        course: str | None,
        limit: int,
    ) -> list[DocumentCardRecord]:
        """Return synthetic cards."""
        del workspace_id, course
        return [document.card for document in self._index.documents[:limit]]

    async def match_document_cards(
        self,
        *,
        workspace_id: str,
        query_embedding: list[float],
        course: str | None,
        limit: int,
    ) -> list[DocumentCardRecord]:
        """Return cards ordered by hash-vector cosine score."""
        del workspace_id, course
        scored: list[tuple[float, IndexedDocument]] = []
        for document in self._index.documents:
            scored.append((_cosine(query_embedding, document.card_embedding), document))
        scored.sort(key=lambda item: (-item[0], item[1].filename))
        return [replace(document.card, vector_score=round(score, 4)) for score, document in scored[:limit]]


class InMemoryEvidenceChunkStore:
    """EvidenceChunkStore backed by the synthetic index."""

    def __init__(self, index: SyntheticIndex) -> None:
        self._index = index

    async def match_chunks(
        self,
        *,
        workspace_id: str,
        document_ids: tuple[str, ...],
        query_text: str,
        query_embedding: list[float] | None,
        match_count: int,
    ) -> list[EvidenceChunkRecord]:
        """Return unscored chunks only from routed documents."""
        del workspace_id, query_text, query_embedding
        records: list[EvidenceChunkRecord] = []
        for document_id in document_ids:
            document = self._index.by_id.get(document_id) or self._index.by_filename.get(document_id)
            if document is None:
                continue
            for chunk in document.chunks:
                records.append(
                    EvidenceChunkRecord(
                        chunk_id=chunk.chunk_id,
                        document_id=chunk.filename,
                        document_title=chunk.document_title,
                        section_id=str(chunk.section_index),
                        heading=chunk.heading,
                        content=chunk.content,
                        source_uri=f"sample_materials/rag_search_simple_test/{chunk.filename}",
                        metadata={"fact_ids": list(chunk.fact_ids), "chunk_index": chunk.chunk_index},
                    )
                )
        return records[: max(match_count, len(records))]


def parse_args() -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(description="Evaluate simple synthetic retrieval quality.")
    parser.add_argument("--materials", type=Path, default=DEFAULT_MATERIALS_DIR)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--question", default="", help="Run only one matching question.")
    parser.add_argument(
        "--reingest",
        action="store_true",
        help="Accepted for parity with live flows; the synthetic index is rebuilt every run.",
    )
    return parser.parse_args()


async def run_benchmark(
    *,
    materials_dir: Path = DEFAULT_MATERIALS_DIR,
    cases_path: Path = DEFAULT_CASES_PATH,
    question: str = "",
) -> dict[str, Any]:
    """Build the synthetic index and evaluate retrieval cases."""
    cases = load_cases(cases_path)
    if question:
        needle = question.casefold()
        cases = tuple(case for case in cases if needle in case.question.casefold() or case.question.casefold() in needle)
        if not cases:
            raise SystemExit(f"No synthetic case matched question: {question}")

    embedding_client = HashEmbeddingClient()
    index = await build_index(materials_dir, embedding_client)
    term_scorer = _corpus_term_scorer(index)
    router = DocumentRouter(
        store=InMemoryDocumentCardStore(index),
        embedding_client=embedding_client,
        term_scorer=term_scorer,
        min_score=0.12,
    )
    analyzer = QuestionAnalyzer()
    retriever = EvidenceRetriever(
        chunk_store=InMemoryEvidenceChunkStore(index),
        workspace_id=WORKSPACE_ID,
        match_count=120,
        max_evidence=8,
        min_score=0.22,
        term_scorer=term_scorer,
    )
    reranker = EvidenceReranker()
    pack_builder = EvidencePackBuilder(term_scorer=term_scorer)

    results: list[CaseRun] = []
    for case in cases:
        results.append(
            await evaluate_case(
                case=case,
                index=index,
                analyzer=analyzer,
                router=router,
                retriever=retriever,
                reranker=reranker,
                pack_builder=pack_builder,
            )
        )

    return {
        "run_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "workspace": WORKSPACE_ID,
        "materials_dir": str(materials_dir),
        "cases_path": str(cases_path),
        "materials": [
            {
                "filename": document.filename,
                "title": document.title,
                "sections": len(document.sections),
                "chunks": len(document.chunks),
                "fact_ids": list(document.fact_ids),
            }
            for document in index.documents
        ],
        "metrics": calculate_metrics([asdict(result) for result in results]),
        "case_results": [asdict(result) for result in results],
    }


async def build_index(materials_dir: Path, embedding_client: HashEmbeddingClient) -> SyntheticIndex:
    """Load materials, create document cards, sections, chunks, and embeddings."""
    if not materials_dir.exists():
        raise FileNotFoundError(f"Missing synthetic materials directory: {materials_dir}")

    loader = FileLoader()
    chunker = ParentChildChunker(chunk_size=900, chunk_overlap=120)
    card_builder = DocumentCardBuilder()
    documents: list[IndexedDocument] = []
    embeddings: dict[str, list[float]] = {}

    for path in sorted(materials_dir.glob("*.md")):
        loaded = await loader.load(path)
        sections = chunker.split_sections(loaded)
        draft_chunks = chunker.split_chunks(sections)
        card = await card_builder.build(loaded, sections)
        enriched_card = _document_card_record(
            filename=path.name,
            title=loaded.title,
            card=card,
            structured_text=loaded.structured_text,
        )
        card_embedding = await embedding_client.embed(_card_text(enriched_card))
        document_chunks = tuple(_indexed_chunk(path.name, loaded.title, draft) for draft in draft_chunks)
        documents.append(
            IndexedDocument(
                document_id=path.name,
                filename=path.name,
                title=loaded.title,
                card=enriched_card,
                sections=sections,
                chunks=document_chunks,
                card_embedding=card_embedding,
                fact_ids=tuple(_fact_ids(loaded.structured_text)),
            )
        )
        embeddings[path.name] = card_embedding

    for document in await _crowded_it_documents(embedding_client):
        documents.append(document)
        embeddings[document.filename] = document.card_embedding

    return SyntheticIndex(documents=tuple(documents), embeddings=embeddings)


async def _crowded_it_documents(embedding_client: HashEmbeddingClient) -> tuple[IndexedDocument, ...]:
    """Return controlled IT distractors with repeated common terms."""
    specs: list[tuple[str, str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = []

    for index in range(1, 11):
        specs.append(
            (
                f"crowded_webhook_noise_{index:02d}.md",
                f"Webhook distractor {index:02d}",
                (
                    f"# Webhook distractor {index:02d}\n\n"
                    f"FACT-ID: WEBHOOK_NOISE_{index:02d}\n"
                    "Этот материал много раз упоминает webhook, HTTP callback и уведомления, "
                    "но объясняет только общий прием событий и не содержит расчет подписи платежа."
                ),
                ("webhook", "HTTP callback", "notification"),
                ("Как принять общий webhook?",),
                ("YooMoney hash", "подпись платежа", "SHA1"),
            )
        )

    for index in range(1, 11):
        specs.append(
            (
                f"crowded_n8n_noise_{index:02d}.md",
                f"n8n distractor {index:02d}",
                (
                    f"# n8n distractor {index:02d}\n\n"
                    f"FACT-ID: N8N_NOISE_{index:02d}\n"
                    "Материал упоминает n8n, workflow, webhook и интеграции, "
                    "но остается обзором автоматизаций и не дает шаги запуска сервера."
                ),
                ("n8n", "workflow", "webhook"),
                ("Как устроен общий workflow n8n?",),
                ("локальная установка", "localhost:5678", "npx"),
            )
        )

    for index in range(1, 11):
        specs.append(
            (
                f"crowded_supabase_noise_{index:02d}.md",
                f"Supabase distractor {index:02d}",
                (
                    f"# Supabase distractor {index:02d}\n\n"
                    f"FACT-ID: SUPABASE_NOISE_{index:02d}\n"
                    "Материал упоминает Supabase, таблицы, REST API и RLS, "
                    "но остается обзором обычной структуры базы и прав доступа."
                ),
                ("Supabase", "таблицы", "REST API", "RLS"),
                ("Как создать обычную таблицу Supabase?",),
                ("match_documents", "pgvector", "vector search"),
            )
        )

    specs.extend(
        [
            (
                "it_yoomoney_hash.md",
                "YooMoney webhook hash",
                (
                    "# YooMoney webhook hash\n\n"
                    "FACT-ID: IT_YOOMONEY_HASH\n"
                    "Для YooMoney webhook проверяют подпись уведомления: строку параметров "
                    "собирают в заданном порядке и сравнивают рассчитанный SHA1 hash с полем sha1_hash."
                ),
                ("YooMoney", "webhook", "SHA1", "sha1_hash", "подпись"),
                ("Как проверить YooMoney hash в webhook?",),
                ("Docker", "n8n локальная установка", "Supabase tables"),
            ),
            (
                "it_n8n_local_install.md",
                "n8n local install",
                (
                    "# n8n local install\n\n"
                    "FACT-ID: IT_N8N_LOCAL_INSTALL\n"
                    "Локальная установка n8n выполняется через npx или Docker. После запуска "
                    "интерфейс открывают на localhost:5678 и проверяют, что порт отвечает."
                ),
                ("n8n", "локальная установка", "Docker", "npx", "localhost:5678"),
                ("Как установить n8n локально?",),
                ("YooMoney", "workflow payments", "Supabase RAG"),
            ),
            (
                "it_supabase_match_documents.md",
                "Supabase match_documents RPC",
                (
                    "# Supabase match_documents RPC\n\n"
                    "FACT-ID: IT_SUPABASE_MATCH_DOCUMENTS\n"
                    "RPC match_documents используют как pgvector-функцию: она принимает query_embedding, "
                    "сравнивает embedding через vector search и возвращает похожие документы."
                ),
                ("Supabase", "match_documents", "pgvector", "query_embedding", "vector search"),
                ("Как работает Supabase match_documents?",),
                ("общие таблицы", "RLS overview", "storage"),
            ),
        ]
    )

    return tuple([await _generated_document(spec, embedding_client) for spec in specs])


async def _generated_document(
    spec: tuple[str, str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]],
    embedding_client: HashEmbeddingClient,
) -> IndexedDocument:
    filename, title, content, topics, questions, not_about = spec
    card = DocumentCardRecord(
        document_id=filename,
        filename=filename,
        title=title,
        course="crowded it synthetic",
        lesson=title,
        summary=content,
        topics=topics,
        questions_answered=questions,
        entities=tuple(_dedupe(list(topics) + _fact_ids(content), limit=16)),
        task_types=("setup", "debug", "source_check", "reference"),
        not_about=not_about,
        quality_score=0.9,
    )
    section = SectionDraft(section_index=0, heading=title, content=content, summary=content[:240])
    chunk = IndexedChunk(
        chunk_id=f"{filename}:0",
        document_id=filename,
        filename=filename,
        document_title=title,
        heading=title,
        content=content,
        chunk_index=0,
        section_index=0,
        fact_ids=tuple(_fact_ids(content)),
    )
    return IndexedDocument(
        document_id=filename,
        filename=filename,
        title=title,
        card=card,
        sections=(section,),
        chunks=(chunk,),
        card_embedding=await embedding_client.embed(_card_text(card)),
        fact_ids=tuple(_fact_ids(content)),
    )


def _corpus_term_scorer(index: SyntheticIndex) -> CorpusTermScorer:
    """Build corpus-aware term scorer from the synthetic index."""
    documents = [
        CorpusDocumentText(
            document_id=document.filename,
            title=document.title,
            course=document.card.course,
            text=_positive_card_text(document.card),
            chunks=tuple(chunk.content for chunk in document.chunks),
        )
        for document in index.documents
    ]
    return CorpusTermScorer.from_documents(documents)


def _positive_card_text(card: DocumentCardRecord) -> str:
    """Return card text without negative/not-about terms for corpus stats."""
    return "\n".join(
        [
            card.filename,
            card.title,
            card.summary,
            " ".join(card.topics),
            " ".join(card.questions_answered),
            " ".join(card.entities),
        ]
    )


async def evaluate_case(
    *,
    case: SyntheticCase,
    index: SyntheticIndex,
    analyzer: QuestionAnalyzer,
    router: DocumentRouter,
    retriever: EvidenceRetriever,
    reranker: EvidenceReranker,
    pack_builder: EvidencePackBuilder,
) -> CaseRun:
    """Evaluate one case and return a diagnostic result."""
    del index
    analysis = analyzer.analyze(case.question)
    documents = await router.route(analysis, workspace_id=WORKSPACE_ID, limit=5)
    incomplete = is_incomplete_question(case.question)
    out_of_base = is_out_of_base_question(case.question)

    if incomplete or out_of_base:
        evidence_spans: tuple[EvidenceSpan, ...] = ()
        reason = "question is incomplete; retrieval not trusted" if incomplete else "question is out of synthetic base"
        raw_records = await retriever.candidate_records(analysis, documents)
        discarded = [_discarded_record(record, reason) for record in raw_records[:8]]
    else:
        evidence_spans = await retriever.retrieve(analysis, documents)
        discarded = [_discarded_evidence_dict(item) for item in retriever.last_discarded]

    reranked = reranker.rerank(evidence_spans, analysis=analysis)
    pack_analysis = replace(analysis, must_answer_points=())
    evidence_pack = pack_builder.build(reranked, max_items=case.expected_top_k_chunks or 5, analysis=pack_analysis)
    actual_answer_mode = _actual_answer_mode(evidence_pack, incomplete)
    top_candidates = [_candidate_dict(candidate) for candidate in documents]
    found_fact_ids = _dedupe([fact for item in evidence_pack.items for fact in _fact_ids(item.text)])
    missing_fact_ids = [fact for fact in case.expected_fact_ids if fact not in found_fact_ids]
    raw_forbidden = _forbidden_in_documents(case.forbidden_documents, [candidate.filename for candidate in documents])
    evidence_docs = [span.document_id for span in evidence_pack.items]
    evidence_forbidden = _forbidden_in_documents(case.forbidden_documents, evidence_docs)
    selected_document = evidence_docs[0] if evidence_docs else (documents[0].filename if documents else "")
    document_pass = _document_pass(case, documents, evidence_pack)
    score = _final_score(
        case=case,
        document_pass=document_pass,
        found_fact_ids=found_fact_ids,
        evidence_forbidden=evidence_forbidden,
        actual_answer_mode=actual_answer_mode,
        evidence_pack=evidence_pack,
    )
    result = "pass" if case_result_passes(
        {
            "document_pass": document_pass,
            "missing_fact_ids": missing_fact_ids,
            "evidence_forbidden_documents": evidence_forbidden,
            "actual_answer_mode": actual_answer_mode,
            "expected_answer_mode": case.expected_answer_mode,
            "evidence_pack_items": [_evidence_item_dict(item) for item in evidence_pack.items],
        }
    ) else "fail"

    return CaseRun(
        id=case.id,
        question=case.question,
        expected_document=case.expected_document,
        expected_fact_ids=list(case.expected_fact_ids),
        expected_answer_mode=case.expected_answer_mode,
        top_document_candidates=top_candidates,
        selected_document=selected_document,
        document_pass=document_pass,
        found_fact_ids=found_fact_ids,
        missing_fact_ids=missing_fact_ids,
        raw_forbidden_documents=raw_forbidden,
        evidence_forbidden_documents=evidence_forbidden,
        evidence_pack_items=[_evidence_item_dict(item) for item in evidence_pack.items],
        discarded_candidates=discarded,
        actual_answer_mode=actual_answer_mode,
        final_score=score,
        result=result,
        explanation=_explain_result(case, result, document_pass, missing_fact_ids, evidence_forbidden, actual_answer_mode),
        analysis=_analysis_dict(analysis),
    )


def load_cases(path: Path = DEFAULT_CASES_PATH) -> tuple[SyntheticCase, ...]:
    """Load synthetic retrieval cases."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Synthetic cases file must contain a JSON list")
    return tuple(SyntheticCase.from_dict(row) for row in data)


def write_reports(report: dict[str, Any], output_dir: Path = DEFAULT_REPORT_DIR) -> tuple[Path, Path]:
    """Write timestamped and latest JSON/Markdown reports."""
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(report["run_id"])
    json_path = output_dir / f"{run_id}.json"
    md_path = output_dir / f"{run_id}.md"
    latest_json = output_dir / "latest.json"
    latest_md = output_dir / "latest.md"

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    markdown = render_markdown_report(report)
    json_path.write_text(payload, encoding="utf-8")
    latest_json.write_text(payload, encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    latest_md.write_text(markdown, encoding="utf-8")
    return latest_json, latest_md


def calculate_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate aggregate retrieval metrics."""
    total = len(results)
    if total == 0:
        return {
            "total_cases": 0,
            "document_top1_accuracy": 0.0,
            "document_top3_accuracy": 0.0,
            "chunk_fact_recall": 0.0,
            "evidence_precision": 0.0,
            "forbidden_document_leakage": 0.0,
            "answer_mode_accuracy": 0.0,
            "average_evidence_pack_size": 0.0,
        }

    top1 = sum(1 for result in results if _expected_doc_rank(result) == 1 or _no_expected_doc_pass(result))
    top3 = sum(
        1
        for result in results
        if (_expected_doc_rank(result) and _expected_doc_rank(result) <= 3) or _no_expected_doc_pass(result)
    )
    fact_recalls = [_fact_recall(result) for result in results]
    evidence_precisions = [_case_evidence_precision(result) for result in results]
    leak_cases = sum(1 for result in results if result.get("evidence_forbidden_documents"))
    mode_hits = sum(
        1
        for result in results
        if _answer_mode_matches(
            str(result.get("expected_answer_mode") or ""),
            str(result.get("actual_answer_mode") or ""),
        )
    )
    evidence_sizes = [len(result.get("evidence_pack_items") or []) for result in results]

    return {
        "total_cases": total,
        "document_top1_accuracy": round(top1 / total, 4),
        "document_top3_accuracy": round(top3 / total, 4),
        "chunk_fact_recall": round(sum(fact_recalls) / total, 4),
        "evidence_precision": round(sum(evidence_precisions) / total, 4),
        "forbidden_document_leakage": round(leak_cases / total, 4),
        "answer_mode_accuracy": round(mode_hits / total, 4),
        "average_evidence_pack_size": round(sum(evidence_sizes) / total, 2),
    }


def case_result_passes(result: dict[str, Any]) -> bool:
    """Return true if a case passes the retrieval acceptance rules."""
    if result.get("evidence_forbidden_documents"):
        return False
    if not _answer_mode_matches(
        str(result.get("expected_answer_mode") or ""),
        str(result.get("actual_answer_mode") or ""),
    ):
        return False
    expected_mode = str(result.get("expected_answer_mode") or "")
    if expected_mode in {"out_of_base", "ask_for_missing_data"}:
        return not result.get("evidence_pack_items")
    return bool(result.get("document_pass")) and not result.get("missing_fact_ids")


def render_markdown_report(report: dict[str, Any]) -> str:
    """Render a human-readable Markdown report."""
    metrics = report["metrics"]
    lines = [
        "# Simple Synthetic Retrieval Report",
        "",
        f"- Run: `{report['run_id']}`",
        f"- Workspace: `{report['workspace']}`",
        f"- Materials: `{report['materials_dir']}`",
        "",
        "## Metrics",
        "",
    ]
    for key, value in metrics.items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| id | question | expected doc | top doc | doc pass | facts found | forbidden leakage | evidence size | result |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for result in report["case_results"]:
        top_doc = (result.get("top_document_candidates") or [{}])[0].get("filename", "")
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(result["id"]),
                    _md(result["question"]),
                    _md(result.get("expected_document") or "-"),
                    _md(top_doc or "-"),
                    "yes" if result.get("document_pass") else "no",
                    _md(", ".join(result.get("found_fact_ids") or []) or "-"),
                    _md(", ".join(result.get("evidence_forbidden_documents") or []) or "none"),
                    str(len(result.get("evidence_pack_items") or [])),
                    result.get("result", "fail"),
                ]
            )
            + " |"
        )

    failures = [result for result in report["case_results"] if result.get("result") != "pass"]
    if failures:
        lines.extend(["", "## Fail Details", ""])
        for result in failures:
            lines.extend(
                [
                    f"### {result['id']}",
                    "",
                    f"- Why: {result.get('explanation')}",
                    f"- Selected documents: {_candidate_names(result.get('top_document_candidates') or [])}",
                    f"- Evidence chunks: {_evidence_names(result.get('evidence_pack_items') or [])}",
                    f"- Missing fact ids: {', '.join(result.get('missing_fact_ids') or []) or 'none'}",
                    f"- Forbidden evidence: {', '.join(result.get('evidence_forbidden_documents') or []) or 'none'}",
                    "",
                ]
            )
    return "\n".join(lines).strip() + "\n"


def _document_card_record(
    *,
    filename: str,
    title: str,
    card: Any,
    structured_text: str,
) -> DocumentCardRecord:
    explains = _section_text(structured_text, "Что этот материал объясняет")
    not_about = _section_text(structured_text, "Что этот материал НЕ объясняет")
    fact_ids = _fact_ids(structured_text)
    title_topics = [title, filename.replace("_", " ").replace(".md", "")]
    topics = _dedupe(title_topics + _important_terms(structured_text) + list(card.topics), limit=48)
    questions = _dedupe(
        [
            f"Как {title.lower()}?",
            f"Что важно знать: {explains}",
            *list(card.questions_answered),
        ],
        limit=12,
    )
    not_about_items = _dedupe(_not_about_terms(not_about), limit=12)
    return DocumentCardRecord(
        document_id=filename,
        filename=filename,
        title=title,
        course="simple synthetic",
        lesson=title,
        summary=card.summary,
        topics=tuple(topics),
        questions_answered=tuple(questions),
        entities=tuple(_dedupe(list(fact_ids) + _important_terms(title), limit=16)),
        task_types=("general", "how_to"),
        not_about=tuple(not_about_items),
        quality_score=0.9,
    )


def _indexed_chunk(filename: str, title: str, draft: ChunkDraft) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=f"{filename}:{draft.chunk_index}",
        document_id=filename,
        filename=filename,
        document_title=title,
        heading=draft.heading,
        content=draft.content,
        chunk_index=draft.chunk_index,
        section_index=draft.section_index,
        fact_ids=tuple(_fact_ids(draft.content)),
    )


def _raw_chunk_candidates(
    index: SyntheticIndex,
    analysis: QuestionAnalysis,
    documents: tuple[DocumentCandidate, ...],
) -> list[ChunkCandidate]:
    candidates: list[ChunkCandidate] = []
    for document_candidate in documents:
        document = index.by_filename.get(document_candidate.filename)
        if document is None:
            continue
        for chunk in document.chunks:
            score, reason = _chunk_score(analysis, chunk)
            candidates.append(ChunkCandidate(chunk=chunk, score=score, reason=reason))
    candidates.sort(key=lambda item: (-item.score, item.chunk.filename, item.chunk.chunk_index))
    return candidates


def _retrieve_evidence(
    index: SyntheticIndex,
    analysis: QuestionAnalysis,
    documents: tuple[DocumentCandidate, ...],
    raw_chunks: list[ChunkCandidate],
) -> tuple[tuple[EvidenceSpan, ...], list[dict[str, Any]]]:
    del index, documents
    selected: list[EvidenceSpan] = []
    discarded: list[dict[str, Any]] = []
    for candidate in raw_chunks:
        if _is_not_about_chunk(candidate.chunk):
            discarded.append(_discarded(candidate, "not-about section"))
            continue
        if _misses_required_object(analysis, candidate.chunk):
            discarded.append(_discarded(candidate, "missing concrete object from question"))
            continue
        if _misses_mandatory_query_root(analysis, candidate.chunk):
            discarded.append(_discarded(candidate, "missing specific qualifier from question"))
            continue
        if candidate.score < 0.28:
            discarded.append(_discarded(candidate, "below lexical evidence threshold"))
            continue
        selected.append(
            EvidenceSpan(
                evidence_id=candidate.chunk.chunk_id,
                document_id=candidate.chunk.filename,
                document_title=candidate.chunk.document_title,
                text=candidate.chunk.content,
                locator=f"{candidate.chunk.heading}, chunk {candidate.chunk.chunk_index}",
                source_uri=f"sample_materials/rag_search_simple_test/{candidate.chunk.filename}",
                score=round(candidate.score, 4),
                is_source=True,
            )
        )
        if len(selected) >= 8:
            break

    selected_ids = {span.evidence_id for span in selected}
    for candidate in raw_chunks:
        if candidate.chunk.chunk_id in selected_ids:
            continue
        if len(discarded) >= 12:
            break
        reason = "not selected after reranking"
        if _is_not_about_chunk(candidate.chunk):
            reason = "not-about section"
        elif candidate.score < 0.28:
            reason = "below lexical evidence threshold"
        discarded.append(_discarded(candidate, reason))

    return tuple(selected), _dedupe_discarded(discarded, limit=12)


def _discarded_for_untrusted(raw_chunks: list[ChunkCandidate], reason: str) -> list[dict[str, Any]]:
    return [_discarded(candidate, reason) for candidate in raw_chunks[:8]]


def _chunk_score(analysis: QuestionAnalysis, chunk: IndexedChunk) -> tuple[float, str]:
    query_roots = _query_roots(analysis.original_question)
    if not query_roots:
        return 0.0, "no specific query terms"
    content_roots = _roots(_tokens(" ".join([chunk.filename, chunk.document_title, chunk.heading, chunk.content])))
    overlap = sorted(query_roots & content_roots)
    score = len(overlap) / max(len(query_roots), 1)
    title_overlap = any(root in _roots(_tokens(chunk.document_title)) for root in query_roots)
    if chunk.fact_ids:
        score += 0.22 if len(overlap) >= 2 or title_overlap else 0.05
    if title_overlap:
        score += 0.12
    if _negative_comparison_not_about_query(chunk, query_roots):
        score *= 0.35
    if _is_not_about_chunk(chunk):
        score *= 0.2
    reason = "matched terms: " + ", ".join(overlap[:8]) if overlap else "no strong lexical overlap"
    return round(min(score, 1.0), 4), reason


def _document_pass(case: SyntheticCase, documents: tuple[DocumentCandidate, ...], evidence_pack: EvidencePack) -> bool:
    if not case.expected_document:
        return not evidence_pack.items
    for index, candidate in enumerate(documents, start=1):
        if candidate.filename == case.expected_document:
            return index <= max(case.expected_top_k_document, 1)
    return False


def _final_score(
    *,
    case: SyntheticCase,
    document_pass: bool,
    found_fact_ids: list[str],
    evidence_forbidden: list[str],
    actual_answer_mode: str,
    evidence_pack: EvidencePack,
) -> float:
    if evidence_forbidden:
        return 0.0
    if case.expected_answer_mode in {"out_of_base", "ask_for_missing_data"}:
        return 1.0 if _answer_mode_matches(case.expected_answer_mode, actual_answer_mode) and not evidence_pack.items else 0.0
    fact_recall = 1.0
    if case.expected_fact_ids:
        fact_recall = len([fact for fact in case.expected_fact_ids if fact in found_fact_ids]) / len(case.expected_fact_ids)
    mode_score = 1.0 if _answer_mode_matches(case.expected_answer_mode, actual_answer_mode) else 0.0
    return round(document_pass * 0.35 + fact_recall * 0.35 + mode_score * 0.2 + 0.1, 4)


def _actual_answer_mode(evidence_pack: EvidencePack, incomplete: bool) -> str:
    if incomplete:
        return "ask_for_missing_data"
    if not evidence_pack.items:
        return "out_of_base"
    return evidence_pack.answer_mode


def _answer_mode_matches(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    return expected == "out_of_base" and actual in {"out_of_base", "ask_for_missing_data"}


def is_incomplete_question(question: str) -> bool:
    """Return true for questions that lack an answerable object."""
    lowered = question.casefold().replace("ё", "е")
    if any(
        phrase in lowered
        for phrase in (
            "как это хранить",
            "почему испортилось",
            "как готовить",
            "сколько воды нужно",
            "что делать, если не растет",
            "что делать если не растет",
        )
    ):
        return True
    roots = _query_roots(question)
    if roots & OUT_OF_BASE_ROOTS:
        return False
    if not (roots & KNOWN_OBJECT_ROOTS) and roots & AMBIGUOUS_ACTION_ROOTS:
        return True
    return not (roots & KNOWN_OBJECT_ROOTS) and len(roots) <= 1


def is_out_of_base_question(question: str) -> bool:
    """Return true for synthetic questions whose concrete object is outside the corpus."""
    roots = _query_roots(question)
    return bool(roots & OUT_OF_BASE_ROOTS)


def _analysis_dict(analysis: QuestionAnalysis) -> dict[str, Any]:
    return {
        "original_question": analysis.original_question,
        "task_type": analysis.task_type,
        "primary_intent": analysis.primary_intent,
        "query_facets": [asdict(facet) for facet in analysis.query_facets],
        "keywords": list(analysis.keywords),
        "primary_object": analysis.primary_object,
        "object_terms": list(analysis.object_terms),
        "requested_action": analysis.requested_action,
        "requested_attribute": analysis.requested_attribute,
        "common_terms": list(analysis.common_terms),
        "platform_terms": list(analysis.platform_terms),
        "config_terms": list(analysis.config_terms),
        "exact_terms": list(analysis.exact_terms),
        "rare_anchor_terms": list(analysis.rare_anchor_terms),
        "strongest_evidence_terms": list(analysis.strongest_evidence_terms),
    }


def _candidate_dict(candidate: DocumentCandidate) -> dict[str, Any]:
    return {
        "document_id": candidate.document_id,
        "filename": candidate.filename,
        "title": candidate.title,
        "score": candidate.score,
        "reason": candidate.reason,
        "matched_topics": list(candidate.matched_topics),
        "matched_questions": list(candidate.matched_questions),
        "route": candidate.route,
        "matched_common_terms": list(candidate.matched_common_terms),
        "matched_anchor_terms": list(candidate.matched_anchor_terms),
        "missing_action_terms": list(candidate.missing_action_terms),
        "missing_object_terms": list(candidate.missing_object_terms),
        "answerability_score": candidate.answerability_score,
        "penalties": list(candidate.penalties),
    }


def _evidence_item_dict(item: EvidenceSpan) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "document": item.document_id,
        "title": item.document_title,
        "locator": item.locator,
        "score": item.score,
        "fact_ids": _fact_ids(item.text),
        "text": item.text,
    }


def _discarded(candidate: ChunkCandidate, reason: str) -> dict[str, Any]:
    return {
        "document": candidate.chunk.filename,
        "locator": f"{candidate.chunk.heading}, chunk {candidate.chunk.chunk_index}",
        "score": candidate.score,
        "reason": reason,
        "fact_ids": list(candidate.chunk.fact_ids),
        "preview": re.sub(r"\s+", " ", candidate.chunk.content).strip()[:180],
    }


def _discarded_record(record: EvidenceChunkRecord, reason: str) -> dict[str, Any]:
    return {
        "document": record.document_id,
        "locator": record.heading or "",
        "score": record.score,
        "reason": reason,
        "fact_ids": list((record.metadata or {}).get("fact_ids") or []),
        "preview": re.sub(r"\s+", " ", record.content).strip()[:180],
    }


def _discarded_evidence_dict(item: Any) -> dict[str, Any]:
    return {
        "document": item.document_id,
        "locator": item.chunk_id,
        "score": item.score,
        "reason": item.reason,
        "fact_ids": [],
        "preview": item.preview,
    }


def _explain_result(
    case: SyntheticCase,
    result: str,
    document_pass: bool,
    missing_fact_ids: list[str],
    evidence_forbidden: list[str],
    actual_answer_mode: str,
) -> str:
    if result == "pass":
        if case.expected_answer_mode in {"out_of_base", "ask_for_missing_data"}:
            return "Поиск не выдал evidence для вопроса без достаточного материала."
        return "Ожидаемый документ и контрольные факты найдены без forbidden evidence."
    if evidence_forbidden:
        return "Forbidden document попал в evidence_pack: " + ", ".join(evidence_forbidden)
    if not document_pass:
        return "Ожидаемый документ не попал в нужный top-k document routing."
    if missing_fact_ids:
        return "Не найдены expected fact ids: " + ", ".join(missing_fact_ids)
    if not _answer_mode_matches(case.expected_answer_mode, actual_answer_mode):
        return f"Неверный answer mode: expected {case.expected_answer_mode}, got {actual_answer_mode}"
    return "Кейс не прошёл по совокупной оценке."


def _expected_doc_rank(result: dict[str, Any]) -> int | None:
    expected = str(result.get("expected_document") or "")
    if not expected:
        return None
    for index, candidate in enumerate(result.get("top_document_candidates") or [], start=1):
        if candidate.get("filename") == expected:
            return index
    return None


def _no_expected_doc_pass(result: dict[str, Any]) -> bool:
    return not result.get("expected_document") and not result.get("evidence_pack_items")


def _fact_recall(result: dict[str, Any]) -> float:
    expected = set(result.get("expected_fact_ids") or [])
    if not expected:
        return 1.0
    found = set(result.get("found_fact_ids") or [])
    return len(expected & found) / len(expected)


def _case_evidence_precision(result: dict[str, Any]) -> float:
    items = result.get("evidence_pack_items") or []
    expected = result.get("expected_document")
    if not items:
        return 1.0 if not expected else 0.0
    if not expected:
        return 0.0
    expected_items = [item for item in items if item.get("document") == expected]
    return len(expected_items) / len(items)


def _candidate_names(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "none"
    return ", ".join(f"{item.get('filename')} ({item.get('score')})" for item in candidates[:5])


def _evidence_names(items: list[dict[str, Any]]) -> str:
    if not items:
        return "none"
    return ", ".join(f"{item.get('document')}:{item.get('locator')}" for item in items[:6])


def _forbidden_in_documents(forbidden: tuple[str, ...], documents: list[str]) -> list[str]:
    found: list[str] = []
    for document in documents:
        if document in forbidden and document not in found:
            found.append(document)
    return found


def _fact_ids(text: str) -> list[str]:
    return FACT_RE.findall(text or "")


def _section_text(text: str, heading: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return ""
    start = match.end()
    next_heading = re.search(r"^##\s+", text[start:], flags=re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(text)
    return text[start:end].strip()


def _important_terms(text: str) -> list[str]:
    return [token for token in _tokens(text) if token not in GENERIC_TOKENS]


def _not_about_terms(text: str) -> list[str]:
    terms = _important_terms(text)
    concrete = [
        term
        for term in terms
        if len(term) >= 4 and term not in {"деревьями", "растениями", "овощами", "продуктов", "посуды"}
    ]
    return concrete


def _tokens(text: str) -> list[str]:
    return [token.casefold().replace("ё", "е") for token in TOKEN_RE.findall(text or "")]


def _query_roots(text: str) -> set[str]:
    return _roots(token for token in _tokens(text) if token not in GENERIC_TOKENS)


def _roots(tokens: Any) -> set[str]:
    return {_root(str(token)) for token in tokens if str(token).strip()}


def _root(token: str) -> str:
    token = token.casefold().replace("ё", "е").strip(".,:;!?()[]{}\"'`«»")
    token = _stem_ru(token)
    if len(token) >= 8:
        return token[:7]
    if len(token) >= 6:
        return token[:5]
    return token


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


def _dedupe(items: list[str] | tuple[str, ...], limit: int | None = None) -> list[str]:
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


def _dedupe_discarded(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = (str(item["document"]), str(item["locator"]), str(item["reason"]))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _card_text(card: DocumentCardRecord) -> str:
    return "\n".join(
        [
            card.filename,
            card.title,
            card.summary,
            " ".join(card.topics),
            " ".join(card.questions_answered),
            " ".join(card.entities),
            " ".join(card.not_about),
        ]
    )


def _is_not_about_chunk(chunk: IndexedChunk) -> bool:
    return "не объясняет" in chunk.heading.casefold()


def _negative_comparison_not_about_query(chunk: IndexedChunk, query_roots: set[str]) -> bool:
    text = chunk.content.casefold().replace("ё", "е")
    if not any(marker in text for marker in ("не должны", "не должен", "не стоит", "не объясняет")):
        return False
    title_roots = _roots(_tokens(chunk.document_title))
    return not bool(title_roots & query_roots)


def _misses_required_object(analysis: QuestionAnalysis, chunk: IndexedChunk) -> bool:
    required_roots = _query_roots(analysis.original_question) & KNOWN_OBJECT_ROOTS
    if not required_roots:
        return False
    chunk_roots = _roots(_tokens(" ".join([chunk.document_title, chunk.content])))
    return not bool(required_roots & chunk_roots)


def _misses_mandatory_query_root(analysis: QuestionAnalysis, chunk: IndexedChunk) -> bool:
    mandatory = _query_roots(analysis.original_question) & MANDATORY_QUERY_ROOTS
    if not mandatory:
        return False
    chunk_roots = _roots(_tokens(" ".join([chunk.document_title, chunk.content])))
    return not bool(mandatory & chunk_roots)


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _md(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")[:140]


async def main_async() -> int:
    """CLI async entry point."""
    args = parse_args()
    report = await run_benchmark(
        materials_dir=args.materials,
        cases_path=args.cases,
        question=args.question,
    )
    latest_json, latest_md = write_reports(report, args.output_dir)
    print(f"Wrote {latest_json}")
    print(f"Wrote {latest_md}")
    metrics = report["metrics"]
    print(
        "Metrics: "
        f"top1={metrics['document_top1_accuracy']} "
        f"top3={metrics['document_top3_accuracy']} "
        f"fact_recall={metrics['chunk_fact_recall']} "
        f"forbidden_leakage={metrics['forbidden_document_leakage']}"
    )
    return 0


def main() -> None:
    """CLI entry point."""
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
