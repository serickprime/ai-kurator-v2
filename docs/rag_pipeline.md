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

Corpus-aware term scoring adjusts routing and evidence selection with term statistics from the active workspace. A term that appears in many documents is treated as a weak/common recall signal. A rare exact term, error, endpoint, command, config field, or function-like token can become a strong anchor without being manually added to a platform dictionary.

Evidence retrieval searches only inside selected documents. It can use vector search, lexical search, page locators, or structured metadata, but it must not pass broad raw candidates to generation.

The evidence pack is the only context visible to answer generation. It should contain short spans, source metadata, and stable locators.

Answer generation must refuse or ask for clarification when the evidence pack is empty or insufficient.

Claim verification checks whether the answer draft is supported by the evidence pack. Unsupported drafts must be revised or replaced with an insufficient-evidence response.

Sources are rendered from used evidence only.

## Key Invariant

Candidate retrieval can be broad. Generation context must be narrow.

## First-Run Checks

Before judging answer quality, verify infrastructure:

```powershell
python scripts/smoke_telegram_config.py
python scripts/smoke_supabase.py
python scripts/smoke_openrouter.py
```

Then ingest the demo corpus:

```powershell
python scripts/ingest_files.py --path .\sample_materials --workspace team --course "demo"
```

The expected manual routing behavior:

- a local n8n install question should route to `n8n_local_install.md`;
- a YooMoney hash/signature question should route to `yoomoney_setup.md`;
- a Supabase `match_documents` question should route to `supabase_match_documents.md`.

If a broad term such as `n8n`, `Docker`, `API`, or `Supabase` pulls in an unrelated lesson as the final source, the pipeline has violated the evidence-first source contract.

After large imports or reindexing, refresh corpus term statistics:

```powershell
python scripts/rebuild_term_statistics.py --workspace team
```

Regular ingestion also attempts this refresh after activating a new document.
