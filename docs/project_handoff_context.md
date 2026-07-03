# Project Handoff Context

This document is the first stop for agents doing nontrivial work in this
repository. It exists so the next agent can understand the project without
reading chat history.

## Identity

- GitHub repository: `serickprime/ai-kurator-v2`
- Local path: `D:\Downloads\ai-kurator-v2`
- Default branch: `main`
- Current main after PR #20: `f08bed0 Add retrieval query quality framework (#20)`

## What This Bot Is

AI Kurator V2 is a local-first Telegram RAG assistant for uploaded course
materials and curated official documentation.

Core flow:

1. Telegram receives a question or an explicit material upload.
2. Uploaded materials are normalized, split into sections/chunks, embedded, and
   stored in Supabase.
3. Official docs are connected only through curated whitelist/candidate flows.
4. The RAG pipeline routes to documents first, retrieves evidence inside those
   documents, builds an evidence pack, generates an answer only from evidence,
   verifies claims, and appends sources from accepted evidence.
5. Telegram commands expose read-only status, source inspection, docs registry
   dashboards, and safe material management.

## Main Components

- `app/main.py` starts the Telegram application.
- `scripts/run_telegram_bot.py` is the production runner; it runs the read-only
  runtime healthcheck before polling and uses a PID lock.
- `scripts/runtime_healthcheck.py` checks config and read-only Supabase/service
  status without polling or writing.
- `app/bot/telegram_bot.py` wires runtime services into Telegram handlers.
- `app/bot/handlers.py` registers commands and keeps Telegram routing thin.
- `app/bot/features/docs_registry.py` formats and handles docs registry UI
  flows.
- `app/ingestion/` handles material loading, cleanup, document cards, sections,
  chunks, service discovery metadata, and indexing.
- `app/external_docs/` handles external docs config, crawling, extraction,
  chunk quality, indexing, and validation.
- `app/docs_registry/` handles curated candidates, preview, activation, and the
  Docs Activation Queue.
- `app/service_registry/` maps service aliases, docs source status, and corpus
  service mentions.
- `app/rag/` contains question analysis, query enrichment, document routing,
  evidence retrieval, reranking, evidence pack building, answer formatting,
  generation, claim verification, and source labels.
- `app/db/` contains the minimal Supabase REST client, repositories, and schema.
- `config/` contains curated YAML config for external docs, service docs
  registry, docs source candidates, and the seed query glossary.
- `tests/` contains unit/regression coverage. Use fake services for new tests.

## Supabase Data And Table Lookup

Do not inspect or change Supabase through ad hoc manual deletes. Start from the
repository schema and repository code:

- schema: `app/db/schema.sql`
- repository access: `app/db/repositories.py`
- REST client: `app/db/supabase_client.py`
- status scripts:
  - `scripts/runtime_healthcheck.py`
  - `scripts/service_docs_status.py`
  - `scripts/external_docs_status.py`
  - `scripts/inspect_ingested_document.py`
  - `scripts/inspect_last_evidence_log.py`

Known core tables/entities:

- `workspaces` - knowledge-base workspace rows.
- `documents` - uploaded/local and external docs documents; use `status` to
  distinguish active/archived and `source_type`/metadata to distinguish
  uploaded material from external docs.
- `document_cards` - document-level summaries/topics for document-first routing.
- `sections` - parent sections with headings/summaries and embeddings.
- `chunks` - evidence chunks with content, heading, metadata, and embeddings.
- `term_statistics` - corpus term statistics used by retrieval scoring.
- `evidence_logs` - debug traces for RAG decisions and final sources.
- `conversations` and `messages` - Telegram conversation state/history.
- `bot_users` - owner/admin/user role rows.
- `user_settings` - optional settings table used by the repository when the
  optional migration exists.

Lookup rules:

- Use read-only scripts first.
- Prefer repository/provider methods over raw table access in app code.
- Uploaded/local materials and official external docs must remain separate.
- Active RAG evidence should come only from active documents/chunks.
- Archive old document versions instead of creating duplicate active versions.
- Do not run schema changes, migrations, or manual data deletion without
  explicit approval.

## Current Capabilities

Already merged into main:

- evidence-first RAG v2;
- Telegram runtime and production runner;
- upload UX and service discovery metadata;
- `/services`;
- `/base_status`;
- `/materials`, `/material <id>`, `/archive_material <id>`;
- `/source_last`, `/archive_source <id>`;
- answer formatting cleanup;
- manual RAG quality smoke suite;
- external docs whitelist/indexing foundation;
- external docs quality gate;
- service/docs registry;
- Docs UI Wizard;
- docs candidates catalog;
- safe `/docs_preview <id>`;
- OpenRouter controlled activation;
- Docs Activation Queue;
- Retrieval Query Quality Framework.

Connected official docs currently include:

- n8n docs;
- Supabase docs;
- OpenRouter docs;
- Telegram Bot API docs.

Use `/docs`, `/services`, `/base_status`, and the status scripts to check the
current live state.

## Docs Registry And Activation Safety

Allowed read-only commands:

- `/docs`
- `/docs_preview <id>`
- `/docs_preview_all`
- `/docs_ready`
- `/docs_activate_ready` without `confirm`
- `/services`
- `/base_status`

Activation commands are dangerous because they can crawl/index/write:

- `/docs_activate <service> confirm`
- `/docs_activate_ready confirm`

Do not run activation confirm, crawl, sync, indexing, or reindex unless the user
explicitly asks for that exact action.

Docs registry rules:

- no arbitrary URLs;
- preview before activation;
- owner/admin confirmation for activation;
- failed, needs_review, and already_connected candidates must not activate in
  batch;
- UI callbacks must not run confirm;
- callbacks must not crawl, sync, index, or write to Supabase.

## Retrieval Query Quality Direction

Do not fix one question point-wise. Build a general retrieval/query quality
layer for uploaded materials and official external docs.

`config/query_glossary.yaml` is a seed glossary:

- it is curated and extensible;
- it is not the final list of all services, topics, or questions;
- it contains retrieval anchors only, not answers;
- it helps bridge ordinary user language to technical terms in evidence;
- new topics should be added through YAML config or reviewed glossary
  candidates, not hardcoded Python branches.

`app/rag/query_enrichment.py` must stay generic: load glossary rules, match
aliases/phrases, add exact/config terms and facets, and preserve the original
question. Glossary content is not evidence and must not bypass the evidence
pack.

Future phase: Glossary Candidate Discovery. It should analyze newly uploaded
materials and official docs, suggest candidate glossary rules, and require
owner/admin approval before applying anything.

## Git Workflow

Use one branch per meaningful block.

Typical flow:

```powershell
git checkout main
git pull
git checkout -b <feature-branch>
```

After work:

```powershell
.\.venv\Scripts\python.exe -m compileall app scripts tests
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts\check_tracked_secrets.py
.\.venv\Scripts\python.exe scripts\runtime_healthcheck.py
git status -sb
git diff --stat
git diff -- . ":!*.env"
```

Commit only intentional files:

```powershell
git add <intentional files>
git commit -m "<clear message>"
git push -u origin <feature-branch>
```

## Push, PR, And Merge Workflow

- Do not merge directly into `main` unless the user explicitly asks.
- Open PRs from feature branch to `main`.
- Do not merge a PR if CI is pending, queued, or failed.
- Prefer squash merge for completed PRs.
- After merge:

```powershell
git checkout main
git pull
git fetch --prune origin
git status -sb
git log --oneline -5
```

- Delete branches only when the user requested it or the merge command was
  explicitly asked to delete the remote branch.

## Secrets Rules

Secrets are not stored in the repository.

Never commit:

- `.env`;
- local credentials;
- Supabase service role keys;
- GitHub PATs;
- Telegram bot tokens;
- logs containing secrets;
- user-level config.

Before committing, run `scripts/check_tracked_secrets.py` and inspect diffs.
Use placeholders only in examples and docs.

## Before Prompt Work

Before creating or rewriting prompts, read `docs/prompting_playbook.md`.

Prompt changes must preserve:

- evidence-first answers;
- no fake certainty;
- no source leakage;
- no answer generation from glossary/config;
- concise, human Telegram style.

## Recommended Manual Smoke

Use `docs/manual_smoke_checklist.md` after meaningful Telegram, RAG, docs
registry, or runtime changes.

High-signal checks:

- `/start`
- `/help`
- `/docs`
- `/services`
- `/base_status`
- `/source_last` after a RAG answer
- one uploaded/local material question
- one n8n official docs question
- one Supabase official docs question
- one OpenRouter official docs question
- one Telegram Bot API official docs question
- one unknown/out-of-base question

Do not run activation confirm during smoke unless explicitly requested.

