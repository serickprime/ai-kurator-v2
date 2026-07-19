# Architecture Guardrails

These rules protect the project from uncontrolled growth and accidental regressions.

## Project context rules

- GitHub repository: `serickprime/ai-kurator-v2`.
- Local path: `D:\Downloads\ai-kurator-v2`.
- Before nontrivial work, read `docs/project_handoff_context.md`.
- Before writing or changing prompts, read `docs/prompting_playbook.md`.
- Do not rely on chat history as the only source of project state.

## Core RAG rules

- AI Kurator V2 is a Telegram evidence-first RAG assistant, not a
  documentation-maintenance bot.
- The user-facing product flow is: accept a Telegram question, understand
  topic/course/service context, retrieve relevant uploaded course evidence and
  approved official documentation when relevant, answer only from accepted
  evidence, show understandable sources, exclude archived versions, and state
  uncertainty when evidence is insufficient.
- Do not change RAG pipeline unless explicitly requested.
- Do not change AnswerGenerator unless explicitly requested.
- Do not change retrieval/router unless explicitly requested.
- Answers must remain evidence-first.
- Sources must come from accepted evidence.
- Do not include raw candidate chunks directly in final answer generation.
- Do not expose UUIDs, raw chunks, debug metadata, or internal implementation
  details to ordinary users.
- Do not fix one user question with one-off Python logic; improve a general retrieval/evidence mechanism.

## Query quality and glossary rules

- `config/query_glossary.yaml` is an extensible seed glossary, not a final topic catalog.
- Query glossary entries are retrieval anchors only; they are not answers and not evidence.
- New services, uploaded-material topics, course topics, and official-doc anchors should be added through config or reviewed glossary candidates.
- Do not hardcode per-service or per-question query enrichment rules in Python.
- Query enrichment must preserve the original user question.
- Query enrichment must not change AnswerGenerator, replace evidence, or bypass evidence-first source flow.
- Automatic glossary candidate discovery may suggest rules, but owner/admin approval is required before applying them.

## Generic Retrieval Fixes

- Treat a failed question as a regression case for a general retrieval problem, not as a reason for a question-specific Python branch.
- Keep normalization generic and store synonyms, translations, user variants, and canonical documentation terms in the data-driven glossary.
- Keep service-specific values in configuration, with traceable provenance for glossary-derived anchors.
- A glossary-derived anchor may affect evidence acceptance only in its matched service context and only when the evidence contains the canonical anchor.
- Never weaken global evidence gates to make one case pass.
- Prefer adding future terms through YAML plus regression tests without changing Python logic.
- Every retrieval fix needs a positive regression, a service-free negative, an unrelated-evidence negative, a different-service or synthetic generic case, and an out-of-base regression check.

## External docs rules

- Do not crawl arbitrary user URLs.
- Do not activate docs from arbitrary URLs.
- Use only curated candidates or approved official domains.
- Preview must happen before activation.
- Owner/admin confirmation is required for activation.
- Do not run `/docs_activate <service> confirm` unless explicitly requested.
- Do not run crawl, sync, indexing, or activation unless explicitly requested.
- External docs and uploaded materials must stay conceptually separate.
- External docs must not be archived through ordinary material commands.
- External documentation is a replaceable knowledge source; zero health
  warnings are not the product goal.
- Do not repair individual stored chunks only to make counters green.
- Dirty documentation fragments require action only when they harm retrieval,
  answers, or citations.
- When a documentation source is broadly broken or stale, fix a generic
  ingestion/extraction problem when one exists, archive or remove the broken
  imported version through an owner-approved safe operation, then fetch and
  index a clean replacement.
- Do not accumulate service-specific Python patches.
- Do not manually edit production chunks.

## Telegram architecture rules

- Telegram handlers should stay thin.
- Business logic should live in feature/service modules.
- UI callbacks must not run activation confirm.
- UI callbacks must not crawl, sync, index, or write to Supabase.
- Buttons should be universal where possible; avoid one top-level button per service.
- Follow-up history is dialog context, not evidence. Future memory support must
  still retrieve fresh accepted evidence for each answer and must not trust
  previous assistant answers as source material.

## Database and secrets rules

- Do not change Supabase schema without explicit approval.
- Start Supabase data/table lookup from `app/db/schema.sql`, `app/db/repositories.py`, and read-only scripts.
- Do not delete Supabase data manually without checking code paths and getting explicit approval.
- Do not touch `.env`.
- Do not reveal secrets.
- Do not print keys, tokens, or service role values.
- Secrets are not stored in this repository.
- Prefer fake services in tests.

## Workflow rules

- One branch = one meaningful block.
- One active roadmap focus at a time.
- Current focus: General Improvement Block 1 - keep Documentation Discovery
  advisory so ordinary questions always continue through the normal RAG answer
  path.
- Phase 7C-B implementation is present in `main`, but post-change production
  validation remains pending.
- Solo-owner mode is the default.
- Start work from fresh `main` unless the user gives a different branch.
- Push feature branches only after requested checks pass.
- GitHub is the durable remote Git store for commits, branches, tags, and
  `main`.
- A Pull Request is not required by default.
- Open a PR only when the owner asks, for schema/migrations, high-risk
  production writes, large risky refactors, or multi-person collaboration.
- For noticeable changes, create a focused feature branch, run checks, commit,
  push the feature branch as backup, locally merge to `main` only after owner
  approval, rerun needed checks, and push `main` normally.
- For small low-risk changes, direct work on `main` is allowed after checking a
  clean state.
- Never force-push.
- Do not delete backup feature branches until published `main` has been
  verified.
- Do not use GitHub UI, GitHub MCP, Playwright, or `gh` only for ordinary
  personal-repository management.
- Prefer squash merge when the user asks to merge.
- Do not start the next roadmap item without explicit instruction.
- Every completed project block must update docs/project_status.md.
- Manual smoke checks should be recorded or summarized before moving to the next risky block.

## Streamlined workflow guardrails

- Keep the GitHub loop short: implement, test, commit/push, open PR when
  requested or required by risk, merge only after explicit owner command, then
  continue only when the owner asks.
- Do not run a separate sanity-check loop after every merge when CI is green,
  the tree is clean, docs use stable baseline policy, and there is no conflict.
- Use manual smoke after runtime or user-visible changes, not after every
  docs-only merge.
- Create docs-only branches or PRs only for blocking context, outdated
  guardrails, misleading roadmap/status docs, architecture decisions, or
  explicit owner requests. Do not create them for latest-commit churn or
  cosmetic cleanup.
- Keep backlog ideas separate from the current branch. Record them as backlog
  or a recommended next prompt instead of mixing unrelated work into the active
  PR.
- During General Improvement Block 1, do not add detection/ranking expansion,
  service-specific rules, production audits, external docs operations, schema
  changes, RAG fixes, Phase 8A, or Phase 8B.
