# Object-First Retrieval Audit

## Already Implemented

- Evidence-first pipeline order is preserved: question analysis -> document router -> evidence retrieval -> reranker -> evidence pack -> answer generation -> claim verification.
- Answer generation receives only `QuestionAnalysis`, `EvidencePack`, user question, and compact dialog context.
- `DocumentRouter` uses document cards before chunk retrieval.
- `EvidencePack` builds sources only from accepted evidence items.
- Synthetic retrieval benchmark covers direct matches, near-miss documents, incomplete questions, and out-of-base questions.

## Stubs And Gaps Found

- `app/rag/evidence_retriever.py` was a stub and raised `NotImplementedError`.
- The synthetic benchmark had its own local chunk scoring and evidence filtering, so it was partially smarter than production code.
- `QuestionAnalysis` did not expose explicit object-first fields such as primary object, object terms, requested action, or requested attribute.
- Router scoring could overvalue generic overlaps from document-card questions/topics.
- `EvidencePackBuilder` selected the first non-empty spans and did not strongly filter weak evidence.

## Benchmark Vs Production Logic

- Before this pass, benchmark retrieval used local `_raw_chunk_candidates`, `_retrieve_evidence`, and ad hoc filters.
- Production `EvidenceRetriever` had no real implementation.
- This created a mismatch: benchmark quality did not prove that the production pipeline could retrieve evidence.

## Needed Changes

- Implement a real scoped `EvidenceRetriever`.
- Make question analysis object-first while keeping existing fields backward compatible.
- Penalize platform-only and general-topic-only document matches.
- Improve fallback document cards so router sees answerable facts from the whole document.
- Make evidence selection narrow and source-safe.
- Keep discarded/raw candidates out of generation.

## Regression Risks

- Over-aggressive object filtering can reject valid partial answers.
- Richer document-card topics can improve recall but may increase near-miss matches if generic terms are not filtered.
- Top-document-only evidence retrieval improves precision for single-topic questions, but compare/source-check questions need multi-document retrieval.
- Russian stemming is deliberately lightweight; it improves common forms but is not a full morphological analyzer.
