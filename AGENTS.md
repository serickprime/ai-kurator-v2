# Agent Instructions

These instructions are for coding agents working in this repository.

## Project Overview

AI Kurator V2 is a local-first Telegram RAG assistant for large educational and technical knowledge bases.

The core architecture is evidence-first:

1. Analyze the user question.
2. Route to likely documents first.
3. Retrieve evidence only inside selected documents.
4. Build a compact evidence pack.
5. Generate the answer only from the evidence pack.
6. Verify claims against the evidence pack.
7. Show sources only from evidence used in the final answer.

This is not the old global chunk-based RAG design. Broad retrieval candidates are allowed, but generation context must stay narrow.

Main entry points:

- `app/main.py` - application entry point.
- `app/config.py` - environment settings.
- `app/bot/` - Telegram runtime, handlers, keyboards, and formatting.
- `app/db/` - Supabase REST client, repositories, and schema draft.
- `app/ingestion/` - loaders, textification, chunking, document cards, and indexing.
- `app/rag/` - evidence-first RAG pipeline.
- `app/llm/` - OpenRouter, embeddings, and vision clients.
- `app/eval/` - evaluation runner, metrics, cases, and reports.
- `docs/` - architecture and operating notes.

## Coding Rules

- Keep changes small and aligned with the current architecture.
- Prefer existing modules and helpers over new abstractions.
- Use type hints for new Python functions.
- Keep user-facing Russian text clear, simple, and conversational.
- Do not add decorative Markdown to bot answers.
- Do not introduce paid APIs unless the user explicitly asks for them.
- New features should fail gracefully in Telegram instead of crashing the bot.
- If a change affects bot behavior, update local `PROJECT_STATUS.md` if it exists, but do not commit it.
- If a change affects setup, commands, or environment variables, update `README.md` and `.env.example`.

## RAG Rules

- Do not copy the old `services/rag.py` design into this repository.
- Do not send raw global candidate chunks to the answer generator.
- Document routing must happen before evidence retrieval for generation context.
- Evidence retrieval can use chunks internally, but the answer model sees only the final evidence pack.
- Sources must be derived from evidence actually used in the answer.
- If there is no evidence, the bot must not imitate an answer from the knowledge base.
- Keep LLM inputs compact. Never send full PDFs, whole source files, or unrelated candidate chunks when evidence spans are enough.

## Supabase Rules

- Before applying schema changes to a live project, inspect the current schema.
- For DDL, use migrations or update `app/db/schema.sql`.
- The planned embedding dimension is `vector(768)` for `nomic-embed-text`.
- Do not switch embedding models for existing data without a reindexing plan.
- RAG search should only use active indexed units.
- Material updates should archive old versions instead of creating duplicate active documents.
- Use `SUPABASE_SERVICE_ROLE_KEY` only in server/local code.
- Never expose service-role keys in frontend code, README examples, logs, or committed files.

## Secrets And Git Safety

- Never commit `.env`, `.venv`, `logs/`, `data/uploads/*`, or `data/processed/*`.
- `.env.example` must contain placeholders only.
- Before committing, check:

```powershell
git status --short --ignored
git grep -n -E "(sb_secret_|ghp_|github_pat_|[0-9]{8,}:[A-Za-z0-9_-]{20,})"
```

If a real secret is found in a tracked file, remove it before committing and tell the user to rotate that key.

## Verification

For Python-only changes, run:

```powershell
.\.venv\Scripts\python.exe -m compileall app scripts tests
.\.venv\Scripts\python.exe -m pytest
```

For RAG behavior changes, test at least one question that should hit known course material and verify that sources are relevant and come only from the used evidence pack.
