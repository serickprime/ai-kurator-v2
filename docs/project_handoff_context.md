# Project Handoff Context

This document is the first stop for agents doing nontrivial work in this
repository. It exists so the next agent can understand the project without
reading chat history.

## Identity

- GitHub repository: `serickprime/ai-kurator-v2`
- Local path: `D:\Downloads\ai-kurator-v2`
- Default branch: `main`
- Latest recorded baseline: PR #22 merged project handoff/status docs into
  `main`.
- Reference commit: `8eac6a5 update project status after handoff merge (#22)`.

The reference commit is not an automatic latest-main pointer. Check exact
current `main` with `git log --oneline -5`, `git status -sb`, or GitHub when a
task depends on the latest commit.

## What This Bot Is

AI Kurator V2 is a Telegram evidence-first RAG assistant for uploaded course
materials and curated official documentation. It is not a
documentation-maintenance bot.

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

The product objective is practical user answers from accepted evidence:
understand topic, course context, and explicitly mentioned service; combine
course material and official documentation evidence when both are relevant;
exclude archived document versions; state uncertainty when evidence is
insufficient; and avoid exposing UUIDs, raw chunks, debug metadata, or internal
implementation details to ordinary users.

## Main Components

- `app/main.py` starts the Telegram application.
- `scripts/run_telegram_bot.py` is the production runner; it runs the read-only
  runtime healthcheck before polling and uses a PID lock.
- `scripts/runtime_healthcheck.py` checks config and read-only Supabase/service
  status without polling or writing.
- `app/bot/telegram_bot.py` wires runtime services into Telegram handlers.
- `app/bot/handlers.py` registers commands, accepts questions/uploads, keeps
  Telegram routing thin, downloads Telegram files under
  `data/uploads/telegram`, and delegates ingestion/RAG work to services.
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
- Phase 7B.2 Telegram Bot API controlled reprocessing is complete: active v2
  target is clean, archived v1 is excluded from active retrieval, required
  terms are present, OpenRouter remains healthy, and Telegram Batch 1 is
  closed.
- Phase 7C-A safe answer-quality harness is complete. It uses a separate
  no-write runtime, disables `EvidenceLogRepository`, sends no Telegram
  messages, wraps Supabase in a read-only adapter, and writes a sanitized
  baseline artifact outside Git.
- Phase 7C-A baseline classification is `functional_blocker_found` with primary
  blocker `evidence_selection_gap`. The next focus is one generic Phase 7C-B
  fix for that blocker.

Known deferred Telegram documentation residue:

- two Webhooks screenshot/page-residue chunks;
- six navigation/footer markers.

This residue is not a current blocker unless a future end-to-end answer audit
shows that it pollutes retrieval, displaces useful evidence, enters final
answer context, appears in final answers, or creates incorrect citations.

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

## Generic Retrieval Fixes

- Treat a failed question as a regression case for a general retrieval problem, not as a reason for a question-specific Python branch.
- Keep normalization generic and store synonyms, translations, user variants, and canonical documentation terms in the data-driven glossary.
- Keep service-specific values in configuration, with traceable provenance for glossary-derived anchors.
- A glossary-derived anchor may affect evidence acceptance only in its matched service context and only when the evidence contains the canonical anchor.
- Never weaken global evidence gates to make one case pass.
- Prefer adding future terms through YAML plus regression tests without changing Python logic.
- Every retrieval fix needs a positive regression, a service-free negative, an unrelated-evidence negative, a different-service or synthetic generic case, and an out-of-base regression check.

## Current Functional Architecture Snapshot

Verified code paths to preserve:

- Telegram question intake: `app/bot/handlers.py`.
- Application wiring: `app/bot/telegram_bot.py`.
- Question analysis and query enrichment: `app/rag/question_analysis.py`,
  `app/rag/query_enrichment.py`, `app/rag/course_resolver.py`.
- Document-first routing: `app/rag/document_router.py`.
- Evidence retrieval and reranking: `app/rag/evidence_retriever.py`,
  `app/rag/reranker.py`.
- Evidence pack: `app/rag/evidence_pack.py`.
- Generation and verification: `app/rag/answer_generator.py`,
  `app/rag/claim_verifier.py`, `app/rag/pipeline.py`.
- Source labels: `app/rag/source_labels.py`.
- Uploads: `app/ingestion/` plus `app/bot/handlers.py`.
- Conversations and settings: `app/db/schema.sql`,
  `app/db/repositories.py`, `app/bot/user_state.py`.

Verified gaps for future phases:

- The normal RAG pipeline uses EvidenceLogRepository and can write an
  `evidence_logs` row during an answer. The Phase 7C-A harness avoids this by
  constructing `EvidenceFirstRagPipeline` with `logger=None`; keep future
  answer-quality audits on this no-write path.
- CourseHintResolver supports data-driven aliases, including aliases built from
  metadata, but normal QuestionAnalyzer currently constructs it without a
  populated alias catalog. A future phase must decide how to load active
  course metadata aliases generically.
- Mixed course-task plus named-service documentation remains an acceptance
  requirement. The Phase 7C-A dynamic mixed fixture was blocked because no
  suitable uploaded material/service pair was found, so mixed routing is not yet
  proven broken.
- Telegram upload handling downloads files under `data/uploads/telegram` and
  passes the local path to ingestion. The handler does not currently remove the
  original temporary file after success or failure. This is Phase 8A.
- `conversations` and `messages` tables exist and ConversationRepository
  supports partial operations, but normal Telegram answer flow does not pass
  previous messages to AnswerGenerator. The current active conversation id is
  primarily process-local, and list/switch/continue/history flows are not
  complete. This is Phase 8B.
- UserSettingsRepository exists, but normal Telegram service wiring currently
  falls back to the in-memory settings repository. Evaluate this during Phase
  8B planning or a later focused block.

## Git Workflow

Solo-owner mode is the default. GitHub is the durable remote Git store for
commits, branches, tags, and `main`; a Pull Request is not required by
default.

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

- Push the feature branch as a backup after checks pass.
- Open a PR only when the owner asks, for schema/migrations, high-risk
  production writes, large risky refactors, or multi-person collaboration.
- For small low-risk changes, direct work on `main` is allowed after checking a
  clean state.
- Locally merge a feature branch into `main` only after explicit owner
  approval.
- Never force-push.
- Do not delete a backup feature branch until published `main` is verified.
- Do not use GitHub UI, GitHub MCP, Playwright, or `gh` only for ordinary
  personal-repository management.
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

## Current Roadmap Focus

- Completed measured block: Phase 7C-A.
- Previous durable source block: Phase 7B.2.
- Phase 7C-B implementation changes and Documentation Discovery MVP code are
  already present in `main` at `02b8693`.
- Phase 7C-B post-change production validation is not recorded and remains
  pending.
- General Improvement Block 1 is complete and merged: Documentation Discovery
  is advisory and every ordinary text question is still passed to the normal
  RAG answer path.
- No next improvement block is active until explicit owner instruction.
- Phase 8A and Phase 8B are recorded but not started.

Do not start discovery detection/ranking expansion, course alias wiring,
mixed-source allocation, prompt changes, citation changes, documentation
ingestion, Phase 8A upload cleanup, or Phase 8B conversation memory without a
new explicit owner instruction.

## Documentation Source Policy

External documentation is a replaceable knowledge source. Zero health warnings
are not the product goal.

- Do not repair individual stored chunks only to make counters green.
- Dirty fragments require action only when they harm retrieval, answers, or
  citations.
- When a documentation source is broadly broken or stale, first identify and
  fix a generic ingestion/extraction problem when one exists, then archive or
  remove the broken imported version through an owner-approved safe operation,
  then fetch and index a clean replacement.
- Do not accumulate service-specific Python patches.
- Do not manually edit production chunks.
- Uploaded materials and official documentation remain conceptually separate.

## Final Reports And Next Prompts

Every Codex or agent final report should follow `docs/prompting_playbook.md`.
It must include the branch, commit, changed files, checks, manual smoke notes,
and confirmations that forbidden actions were not run.

End every final report with `Recommended next prompt`:

- what should be done next;
- why it is the logical next step;
- why the agent is not starting it automatically;
- the guardrails that matter for that next step;
- a copy-paste prompt the owner can send if they approve the next block.

This is only a recommendation. Agents must not start the next roadmap item,
open a PR, merge, activate docs, crawl, sync, index, reindex, migrate, or begin
any other next block without an explicit owner command.

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
