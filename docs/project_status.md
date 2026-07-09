# Project Status

## Latest recorded stable project milestone

This file records project state and completed milestones. It is not an
automatic pointer to the latest Git commit.

Latest recorded baseline:

- PR #22 merged: project handoff/status docs are in `main`.
- Reference commit: `8eac6a5 update project status after handoff merge (#22)`.

The reference commit is a stable context point, not a field that must be
updated after every docs-only or status-only merge. Check the exact latest
`main` commit with `git log --oneline -5`, `git status -sb`, or GitHub when the
exact commit matters for a task.

## Current project state

The bot is a Telegram RAG assistant with an evidence-first RAG v2 architecture.

Core state:

- RAG v2 evidence-first pipeline works.
- Telegram runtime works.
- Production runner exists.
- Upload materials flow works.
- Materials management works.
- Last answer sources management works.
- Answer formatting cleanup is in place.
- Manual RAG quality smoke suite exists.
- External Docs Registry v2 architecture is documented.
- `/docs` dashboard works.
- Docs UI Wizard works.
- Candidates catalog exists.
- `/docs_preview <id>` works.
- Candidate QA report exists.
- OpenRouter controlled activation was completed successfully.
- OpenRouter docs are indexed as official `external_docs`.
- `/source_last` confirms OpenRouter as official external docs source after OpenRouter questions.
- Project control docs are present in the repository.
- Docs Activation Queue is merged.
- Telegram Bot API docs were activated manually by owner/admin through `/docs_activate_ready confirm`.
- Telegram Bot API docs are indexed as official `external_docs`.
- Retrieval Query Quality Framework is merged and uses a generic seed glossary for retrieval-only query enrichment.
- Project handoff context docs are merged into `main`.
- `docs/project_handoff_context.md` and `docs/prompting_playbook.md` are available in `main`.
- Final reports require a `Recommended next prompt` block with rationale, guardrails, and a copy-paste prompt.
- Handoff sanity check passed: a new agent can restore project context from repository docs without chat history.
- Answer formatting postprocessing strips leaked `Evidence:` support artifacts and rewrites wide API parameter tables into Telegram-friendly lists.
- Phase 4A read-only Glossary Candidate Discovery MVP suggests retrieval anchors from existing glossary, term statistics, evidence logs, and active document metadata without applying changes automatically.
- Phase 4A quality cleanup filters noisy candidate anchors and marks sensitive-looking candidates for separate review.
- Phase 4B owner/admin CLI review/apply MVP is complete.
- Phase 4B reviewed glossary additions batch 1 is in `config/query_glossary.yaml`.
- Phase 5A read-only Service-aware Suggestions MVP is merged and verified.
- Phase 5B owner/admin Telegram preview integration is merged and verified.
- Phase 6A read-only Docs Source Health/Stale Report is merged and verified.
- Phase 6B owner/admin Telegram preview for docs health is merged and verified.
- Phase 7A offline source-quality remediation for OpenRouter and Telegram Bot API is the current implementation focus.

## Completed PRs

- PR #1 — external docs whitelist/indexing foundation.
- PR #2 — runtime deployment stabilization.
- PR #3 — slash command fallback.
- PR #4 — Telegram runtime UX cleanup.
- PR #5 — production runner.
- PR #6 — materials management.
- PR #7 — source_last/source archive.
- PR #8 — answer formatting quality.
- PR #9 — RAG quality smoke suite.
- PR #10 — orphan headings cleanup.
- PR #11 — External Docs Registry v2 plan.
- PR #12 — read-only `/docs` dashboard.
- PR #13 — external docs candidates catalog.
- PR #14 — external docs candidate preview.
- PR #15 — external docs candidate QA report.
- PR #16 — controlled OpenRouter docs activation flow.
- PR #17 — Docs UI Wizard.
- PR #18 — project control pack.
- PR #19 — Docs Activation Queue.

- PR #20 - Retrieval Query Quality Framework.
- PR #21 - Project handoff context and prompting playbook.
- PR #22 - Project status update after handoff merge.
- PR #23 - Stable project status baseline policy.
- PR #24 - Answer formatting artifact cleanup.
- PR #25 - Phase 4A read-only Glossary Candidate Discovery MVP.
- PR #26 - Phase 4A glossary candidate quality cleanup.
- PR #27 - Phase 4B glossary candidate review/apply flow.
- PR #28 - Phase 4B minimal-diff glossary output writer.
- PR #29 - Reviewed glossary additions batch 1.
- PR #30 - Phase 5A read-only Service-aware Suggestions MVP.
- PR #31 - Phase 5B service suggestion admin preview.
- PR #32 - Phase 6A read-only Docs Source Health/Stale Report.
- PR #33 - Phase 6B docs health admin preview.

## Latest completed project block

Phase 6B was completed through PR #33:

- owner/admin-only `/docs_health` Telegram preview is available;
- `/docs_health`, `/docs_health openrouter`, and
  `/docs_health telegram_bot_api` show read-only docs health/stale status;
- handlers stay thin and delegate formatting to `app/bot/features`;
- ordinary user RAG flow is unchanged;
- automatic refresh remains disabled;
- no refresh, activation, crawl, sync, indexing, reindex, Supabase writes,
  migrations, RAG pipeline, AnswerGenerator, retrieval/router, schema, or
  normal user flow changes were introduced.

## Current focus

Current active roadmap focus:

- Phase 7A - offline source-quality remediation for OpenRouter and Telegram Bot API.
- Current branch: `phase7a-source-quality-cleanup`.
- Until Phase 7A is merged, do not start real source reprocessing, docs
  refresh/indexing, Supabase setup docs, MCP, or unrelated work unless the
  owner explicitly changes focus.

`docs/project_status.md` tracks project state and stable milestones, not an
exact latest-main pointer after every technical docs merge. Do not create
docs-only PRs only to update latest commit values or for cosmetic cleanup.

## Next recommended

- open a PR for `phase7a-source-quality-cleanup` after the branch is
  ready and pushed;
- check CI and mergeability after the PR is open;
- merge only after explicit owner command;
- do not run a separate sanity-check/docs loop by default after merge unless
  there is a concrete conflict or user-visible runtime risk.

- optional retrieval-quality manual smoke when a future runtime/query enrichment
  change needs it:
  - `Новая тема`
  - `как отправить сообщение через Telegram Bot API?`
  - `/source_last`
- verify the answer uses `telegram_bot_api_docs`;
- verify accepted evidence includes `sendMessage`, `chat_id`, and `text`;
- test n8n/OpenRouter/Supabase glossary cases from `docs/manual_smoke_checklist.md`;
- verify `/base_status` and `/docs` do not show unexplained bare `FAIL`.

## Retrieval quality principle

Do not fix one user question with one-off code. If a natural-language question misses technical docs terms, improve a general retrieval/evidence mechanism and add regression tests for the class of questions.

The knowledge base will keep growing with uploaded materials, courses, service docs, and official docs. Query quality should grow through reviewed glossary/config changes and future candidate discovery, not by adding service-specific `if` branches to Python.

Future Glossary Candidate Discovery should:

- analyze new uploaded materials and external docs;
- extract candidate methods, parameters, node names, endpoints, and recurring technical terms;
- group candidates by service/source;
- suggest glossary updates to the owner/admin;
- apply nothing automatically without owner/admin approval.

Phase 4A scope:

- produce an owner-facing read-only report of suggested glossary candidates;
- keep `config/query_glossary.yaml` unchanged until a future review/apply block;
- write nothing to Supabase;
- scale retrieval quality through reviewed anchors instead of hardcoded one-question fixes.

Phase 4B scope:

- export a manual owner/admin review file from Phase 4A candidates;
- validate pending, approved, rejected, and edited decisions;
- build an apply plan without changing config by default;
- write reviewed output to `reports/` or `tmp/` by default;
- require both `--write-config` and `--confirm-reviewed-apply` before direct
  writes to `config/query_glossary.yaml`;
- require separate `allow_sensitive_apply: true` for sensitive-review
  candidates;
- no Telegram UI in this block.

Phase 5A scope:

- detect service mentions from registry aliases, docs candidates, query
  glossary aliases, and a small detection-only config seed;
- return read-only owner/admin suggestions for known services whose docs are
  missing or inactive;
- return `supported-active` without owner suggestion when docs are already
  active;
- return unknown or ambiguous statuses without confident auto-action;
- no Telegram UI, Supabase writes, config writes, crawl, sync, indexing,
  reindex, activation, migrations, RAG pipeline changes, retrieval/router
  changes, or AnswerGenerator changes.

Phase 5B scope:

- add an explicit owner/admin-only Telegram command for service suggestion
  preview;
- keep ordinary user questions on the normal RAG path;
- keep handlers thin by delegating preview formatting and detection to a
  feature/service module;
- do not auto-register docs, call `/docs_preview`, activate, crawl, sync,
  index, reindex, write config, or write Supabase.

Phase 6A scope:

- add a read-only service-layer and CLI report for docs source health and
  staleness;
- show registered source, service, active state, last-known status/reason,
  timestamps if available, staleness, counts, owner-review need, and safe next
  action;
- keep staleness separate from operational WARN/FAIL;
- do not run refresh, activation, crawl, sync, indexing, reindex, migrations,
  Supabase writes, schema changes, Telegram UI, RAG pipeline, AnswerGenerator,
  or retrieval/router changes.

Phase 6B scope:

- add an explicit owner/admin-only Telegram command for the same docs health
  report;
- support `/docs_health` and `/docs_health <service_id>` as read-only previews;
- keep handlers thin by delegating report formatting to a feature module;
- keep ordinary user questions on the normal RAG path;
- do not refresh, repair, activate, crawl, sync, index, reindex, write
  Supabase, run migrations, change docs status, add action callbacks, or change
  RAG pipeline, AnswerGenerator, retrieval/router, or schema.

Phase 7A scope:

- improve external docs extraction/cleaning and quality validation offline for
  current OpenRouter WARN and Telegram Bot API FAIL classes;
- use small sanitized fixtures and unit/regression tests;
- remove generator/page-template boilerplate, raw page HTML, navigation/footer,
  and cookie chrome while preserving useful endpoints, method names,
  parameters, code blocks, and safe inline HTML examples;
- do not crawl, refresh, sync, index, reindex, activate, write Supabase, run
  migrations, change existing runtime rows, or change RAG pipeline,
  AnswerGenerator, retrieval/router, query enrichment, or normal user flow.

Phase 7B future scope:

- owner-approved reprocessing of affected sources after Phase 7A is merged;
- confirm exact command and runtime environment before any refresh/indexing;
- do not start automatically from Phase 7A.

## Later roadmap

Service-aware suggestions after Phase 5B:

- optional UX refinement for owner/admin preview if manual smoke shows it is
  needed;
- possible curated docs candidate workflow for owner-approved missing services;
- do not auto-index from a normal user question.

Maintenance after Phase 6B:

- disable docs source;
- owner-approved reprocessing for OpenRouter WARN and Telegram Bot API FAIL
  after Phase 7A code is merged.

## Needs review

- Claude Code: preview failed because of redirect issue.
- aiogram: preview found only 1 page.
- Ollama: risk level review.
- Dokploy: risk level review.

## Connected external docs

- n8n docs
- Supabase docs
- OpenRouter docs
- Telegram Bot API docs

## Important reminder

Do not run activation, crawl, sync, or indexing unless the user explicitly asks for that exact action.
