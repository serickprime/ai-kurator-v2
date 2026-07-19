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
- Phase 7A offline source-quality remediation for OpenRouter and Telegram Bot API is merged.
- Phase 7B.0 safe source-scoped reprocessing preparation tooling is merged.
- Phase 7B.1 OpenRouter controlled activation and follow-up safe cleanup were owner-approved and technically completed.
- Phase 7B.1b generic safe obsolete-page reconciliation planning is merged and smoke-tested.
- Phase 7B.1c recovered the OpenRouter production discovered snapshot from activation logs without a new crawl.
- Phase 7B.1d reviewed the three remaining OpenRouter old active v1 pages.
- Phase 7B.1e recorded owner decisions in a local reviewed artifact outside Git.
- Phase 7B.1f split remediation into an archive plan for one superseded page and a separate targeted reprocessing plan for two keep-active pages.
- Phase 7B.1g-A generic reviewed one-document external-doc archive tooling is merged and smoke-tested.
- Phase 7B.1i-A archived the superseded old OpenRouter `mcp-server` document by exact owner approval; successor remained active and child rows were preserved.
- Phase 7B.1g-B generic reviewed key-scoped external-doc reprocessing tooling is merged and smoke-tested.
- Phase 7B.1i-B stopped safely before writes because Service Tiers resolved to a different canonical key.
- Phase 7B.1j-B confirmed Service Tiers as a canonical relocation from `features/service-tiers` to `guides/features/service-tiers`; App Attribution remains active v1 and unchanged.
- Phase 7B.1g-C generic reviewed canonical relocation tooling was completed as part of the Phase 7B remediation sequence.
- Phase 7B.2 Telegram Bot API controlled reprocessing is complete: active v2 target is clean, archived v1 is excluded from active retrieval, required terms are present, OpenRouter remains healthy, and Telegram Batch 1 is formally closed.
- Remaining Telegram Bot API residue is deferred: two Webhooks screenshot/page-residue chunks and six navigation/footer markers. They are not blockers unless a future end-to-end answer audit shows that they pollute retrieval, displace useful evidence, enter final answer context, appear in final answers, or create incorrect citations.
- Phase 7C-A safe answer-quality harness is complete. The harness uses the real RAG components through a separate no-write runtime with `EvidenceLogRepository` disabled, Telegram sending disabled, read-only Supabase guarded by an allowlisted adapter, and a resumable sanitized JSON artifact outside Git.
- Phase 7C-A baseline result: `functional_blocker_found`, primary blocker `evidence_selection_gap`. Product WARN cases were `n8n_docs` and `openrouter_docs` because selected documents existed but no accepted evidence contained the expected high-signal terms. `mixed_course_service_auto` was blocked by lack of a suitable uploaded-material/service fixture, not classified as a proven product failure.
- Phase 7C-A did not show dirty Telegram documentation residue entering final answers or citations. Remaining Webhooks/navigation residue stays deferred.
- `main` at `02b8693` already contains the Phase 7C-B implementation changes
  and the Documentation Discovery MVP code. The repository previously did not
  record this merge consistently.
- Phase 7C-B is not yet closed: no post-change production baseline artifact is
  recorded, so the real-corpus effect remains unverified.
- Documentation Discovery remains feature-flagged off by default. Production
  suggestions-table availability, search-provider configuration, and one
  owner-approved manual Telegram smoke are now recorded. No migration was
  applied during verification, and discovered candidates remain pending until
  owner review.

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
- PR #34 - Phase 7A offline source-quality remediation.
- PR #35 - Phase 7B.0 safe docs reprocessing preparation.
- PR #36 - Phase 7B.1b safe docs reconciliation planning.
- PR #37 - Phase 7B.1g-A reviewed external docs archive tooling.
- PR #38 - Phase 7B.1g-B reviewed external docs reprocessing tooling.
- PR #39 - reviewed reprocessing CLI document id parsing fix.
- PR #40 - reviewed external docs canonical relocation tooling.
- PR #41 - generic external docs navigation cleanup fix.
- PR #42 - safe incomplete draft cleanup tooling.
- PR #43 - reviewed reprocessing confirmation hardening.
- PR #44 - raw HTML validation refinement for documented markup.
- PR #45 - placeholder and documented markup validation fix.
- PR #46 - bounded documented syntax validation finalization.
- PR #47 - documented checked-checkbox syntax compatibility.
- PR #48 - Documentation Discovery MVP implementation.

## Current implementation block

General Improvement Block 1 - advisory Documentation Discovery answer
continuity:

- started by explicit owner request on 2026-07-19;
- implementation merged into `main` after owner approval;
- every ordinary text question continues through the normal evidence-first RAG
  answer flow even when discovery creates a pending docs suggestion;
- a safe discovery notice is advisory and is sent after the answer;
- discovery/search failures remain fail-open for the user answer path;
- no detection/ranking expansion, crawl, sync, indexing, activation, schema
  change, production write, or AnswerGenerator/retrieval change is included.
- owner-approved production preflight and one manual Telegram smoke completed
  on 2026-07-19 against `main` commit `5889755`;
- the read-only preflight confirmed the suggestions table and configured search
  provider without applying a migration;
- the smoke question for an unconnected service received the normal
  `ask_for_missing_data` RAG result first and the safe discovery notice second;
- suggestions for the smoke service changed from zero to exactly one `pending`
  record, and neither new Telegram reply exposed a URL, confidence, UUID, or
  internal diagnostic fields;
- the smoke polling process was stopped after verification; no crawl, sync,
  indexing, reindex, activation, schema change, or `.env` change was run.

## Latest completed project block

Phase 7C-A is complete:

- reusable harness: `scripts/run_answer_quality_baseline.py`;
- main implementation: `app/rag/quality_harness.py`;
- focused tests: `tests/test_answer_quality_harness.py`;
- future baseline command pattern:
  `.\.venv\Scripts\python.exe scripts\run_answer_quality_baseline.py --output <external-json-path> --resume --answer-mode cheap --confirm-read-only-production`;
- artifact stays outside Git, for example under
  `D:\AI_Kurator_Backups\ai-kurator-v2\phase7c`;
- no-write boundary from the completed baseline: evidence logging disabled,
  Telegram sending disabled, Supabase write attempts 0, blocked write calls 0,
  non-allowlisted RPC attempts 0;
- overall classification: `functional_blocker_found`;
- primary blocker: `evidence_selection_gap`;
- next focused phase: Phase 7C-B - one generic fix for
  `evidence_selection_gap`.

Previous durable milestone, Phase 7B.2:

- Telegram Bot API controlled reprocessing was completed through the
  owner-approved safe path;
- active v2 Telegram Bot API target is clean;
- archived v1 Telegram Bot API documents are excluded from active retrieval;
- required Telegram Bot API terms are present;
- OpenRouter remains healthy;
- Telegram Batch 1 is formally closed with classification
  `batch1_closed_target_clean_remaining_webhooks_residue`;
- known Webhooks/page-residue and navigation/footer fragments are deferred as
  non-blocking until functional evidence shows user-facing harm.

## Current focus

Current active roadmap focus:

- General Improvement Block 1 is implemented and merged; no next improvement
  block is active until explicit owner instruction.
- Phase 7C-B implementation is already present in `main`, but its post-change
  production baseline remains pending and must not be reported as complete.
- Do not combine detection/ranking expansion, service-specific rules,
  documentation ingestion, prompts, schema, upload lifecycle, or conversation
  memory in this branch.

`docs/project_status.md` tracks project state and stable milestones, not an
exact latest-main pointer after every technical docs merge. Do not create
docs-only PRs only to update latest commit values or for cosmetic cleanup.

## Next recommended

- do not repeat the Documentation Discovery production smoke unless a future
  change needs it; the owner-approved run is now recorded;
- handle Phase 7C-B production validation and generic Docs Discovery
  detection/ranking improvements as separate future blocks;
- keep Phase 8A and Phase 8B recorded but not started.

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

Phase 7B.0 scope:

- add read-only source-scoped preflight planning before any reprocessing;
- create and verify local baseline manifests with checksums and drift checks;
- expose reusable execution precondition validation for future owner-approved
  OpenRouter reprocessing;
- do not run `/docs_activate`, activation, crawl, sync, indexing, reindex,
  rollback writes, migrations, Supabase writes, or real production backup
  creation in the implementation block.

## Documentation source policy

Official external documentation is a replaceable knowledge source, not the
product goal. Zero health warnings are not the goal by themselves.

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

## Functional gaps recorded for future phases

- Phase 7C-A: build a safe no-write end-to-end answer harness. The normal RAG
  runtime uses EvidenceLogRepository and can write an `evidence_logs` row.
- Phase 7C-B: choose exactly one primary blocker from the Phase 7C-A baseline
  and fix it generically.
- Phase 8A: Telegram downloads uploads under `data/uploads/telegram`; the
  current handler ingests the local path but does not remove the original
  temporary file after success or failure.
- Phase 8B: `conversations` and `messages` tables exist and repository support
  is partial, but normal Telegram answer flow does not pass previous messages
  to AnswerGenerator. The current active conversation id is primarily
  process-local, and list/switch/continue/history flows are not complete.
- User settings persistence: UserSettingsRepository exists, but normal
  Telegram service wiring currently falls back to the in-memory settings
  repository. Evaluate this during Phase 8B planning or a later focused block.

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
