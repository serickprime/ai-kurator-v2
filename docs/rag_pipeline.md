# RAG Pipeline

## Flow

```text
question
-> question analysis
-> document router
-> evidence retrieval inside selected documents
-> reranker
-> evidence pack
-> answer generation
-> claim verification
-> final answer
```

## Stage Responsibilities

Question analysis extracts compact intent, keywords, entities, and constraints.

Document routing compares the analysis with document cards and selects a small document set.

Evidence retrieval searches only inside selected documents. It can use vector search, lexical search, page locators, or structured metadata, but it must not pass broad raw candidates to generation.

The evidence pack is the only context visible to answer generation. It should contain short spans, source metadata, and stable locators.

Answer generation must refuse or ask for clarification when the evidence pack is empty or insufficient.

Claim verification checks whether the answer draft is supported by the evidence pack. Unsupported drafts must be revised or replaced with an insufficient-evidence response.

Sources are rendered from used evidence only.

## Key Invariant

Candidate retrieval can be broad. Generation context must be narrow.
