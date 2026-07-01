# Object-First Retrieval Improvement Report

## Changed Files

- `app/rag/types.py`
- `app/rag/question_analysis.py`
- `app/rag/document_router.py`
- `app/rag/evidence_retriever.py`
- `app/rag/reranker.py`
- `app/rag/evidence_pack.py`
- `app/rag/answer_generator.py`
- `app/rag/claim_verifier.py`
- `app/rag/pipeline.py`
- `app/ingestion/document_cards.py`
- `scripts/evaluate_retrieval_simple_synthetic.py`
- `tests/test_evidence_retriever.py`
- `tests/test_question_analysis.py`
- `tests/test_simple_synthetic_retrieval_eval.py`

## Interfaces Preserved

- Pipeline order remains evidence-first.
- `AnswerGenerator.generate(...)` and `ClaimVerifier.verify(...)` signatures were not changed.
- Generation still receives only `QuestionAnalysis`, `EvidencePack`, user question, and compact dialog context.
- Raw candidates, discarded candidates, and document candidates are not passed to answer generation.
- Sources still come only from `EvidencePack.source_matches`.

## Baseline Metrics

| Metric | Baseline |
|---|---:|
| document_top1_accuracy | 0.7895 |
| document_top3_accuracy | 0.9474 |
| chunk_fact_recall | 0.7237 |
| evidence_precision | 0.8509 |
| forbidden_document_leakage | 0.0 |
| answer_mode_accuracy | 0.8947 |
| average_evidence_pack_size | 1.68 |

## New Metrics

Latest synthetic run:

| Metric | Current |
|---|---:|
| total_cases | 38 |
| document_top1_accuracy | 0.9737 |
| document_top3_accuracy | 1.0 |
| chunk_fact_recall | 1.0 |
| evidence_precision | 0.9789 |
| forbidden_document_leakage | 0.0 |
| answer_mode_accuracy | 1.0 |
| average_evidence_pack_size | 2.13 |

All 38 synthetic cases pass.

## What Improved

- Implemented scoped production `EvidenceRetriever` with Supabase RPC support and safe scoped fallback.
- Added object-first fields to `QuestionAnalysis`: `primary_object`, `object_terms`, `requested_action`, `requested_attribute`, and `generic_terms`.
- Improved Russian lightweight stemming for common noun/adjective forms.
- Reduced generic-topic matches in `DocumentRouter`.
- Added stronger object coverage scoring and penalties for weak object matches.
- Made evidence retrieval narrow for regular questions: only the top routed document is used for evidence, while compare/source-check can use multiple documents.
- Added deterministic reranking with bonuses for object/action/constraint matches and supporting fact markers.
- Made evidence packs compact by default and added explicit `out_of_base` handling.
- Brought the synthetic benchmark closer to production code by using production `EvidenceRetriever`.
- Added regression tests for retriever scoping and benchmark quality gates.

## Remaining Risks

- The Russian stemmer is lightweight and should eventually be replaced or complemented by PostgreSQL FTS dictionaries, trigram similarity, or a proper morphology library.
- Top-document-only evidence retrieval is intentionally precision-first; some future multi-document non-compare questions may need an explicit multi-source intent.
- Supabase fallback table scan is scoped to routed document IDs, but it is less efficient and less relevant than RPC search.
- `out_of_base` is now supported in code and prompt docs, but downstream UI copy may still treat it like `ask_for_missing_data`.

## Not Done Deliberately

- Did not rewrite the RAG pipeline.
- Did not copy old `services/rag.py`.
- Did not expand answer generation context.
- Did not add LLM reranking as a required dependency.
- Did not change Supabase schema.
