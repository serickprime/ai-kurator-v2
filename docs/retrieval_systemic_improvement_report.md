# Universal Evidence Retrieval Improvement Report

## Scope

This update improves the universal retrieval process, not a single benchmark question.
The evidence-first architecture remains unchanged:

question -> QuestionAnalysis -> DocumentRouter -> EvidenceRetriever -> Reranker -> EvidencePack -> AnswerGenerator -> ClaimVerifier

Answer generation still receives only QuestionAnalysis, EvidencePack, the user question, and compact dialog context. Raw candidates, discarded candidates, document candidates, course hints, and domain hints are not passed to the generation prompt.

## What Changed

- Added a structured QueryPlan with expected content types, source priority, soft course/domain hints, action/object/symptom/constraint terms, exact/rare/common terms, evidence requirements, ambiguity, external-doc needs, and source requirement.
- Added CourseHintResolver as a data-driven soft routing helper. Course hints are routing signals only and never evidence.
- Expanded DocumentRouter scoring with content type match, course hint bonus, domain hint bonus, rare anchor match, action/object match, symptom match, constraint match, common-term-only penalty, wrong-content-type penalty, near-miss penalty, and not-about penalty.
- Added score_breakdown and richer route reasons to DocumentCandidate.
- Expanded EvidenceRetriever diagnostics with vector/full-text/trigram/RPC score breakdown, object/action/symptom/constraint/anchor support, source URI propagation, and scoped retrieval diagnostics.
- Made EvidencePackBuilder stricter and explainable through EvidenceDecision records: accepted, partial, discarded. Only selected accepted/needed partial spans enter EvidencePack.
- Added pipeline debug payload for query_plan, selected documents, score breakdown, accepted evidence, discarded evidence, answer mode, and evidence decisions.
- Extended synthetic eval reports with query_plan, content type, matched content types, score breakdown, and evidence decisions.

## Important Guarantees

- Questions without an explicit course filter can search the workspace instead of being forced into one course.
- Course hints are soft scope: they can boost routing, but they do not override answerability.
- Course and domain hints are not sources and are not sent to the answer-generation prompt.
- A common platform term alone is not enough to create strong document or evidence support.
- Wrong content type is penalized before evidence is packed.
- Evidence retrieval searches chunks only inside selected routed documents.
- Sources are created only from EvidencePack.source_matches.
- Out-of-base or missing-input modes do not create fake sources.

## Verification

Commands run on Windows:

```powershell
.\.venv\Scripts\python.exe -m compileall app scripts tests
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts\evaluate_retrieval_simple_synthetic.py --reingest
```

Results:

- compileall: passed
- pytest: 82 passed
- synthetic retrieval eval:
  - document top1 accuracy: 1.0
  - document top3 accuracy: 1.0
  - chunk fact recall: 1.0
  - forbidden document leakage: 0.0

## Notes

The changes intentionally avoid service-specific hardcoded routing rules. The added markers are generic content-type signals such as homework, review rules, course catalog, official docs, student case, and platform navigation.
