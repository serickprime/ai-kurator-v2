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
- Phase 5A read-only Service-aware Suggestions MVP is the current implementation focus.

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

## Latest completed project block

Phase 4B was completed through PR #27, PR #28, and PR #29:

- owner/admin review files can be exported, validated, planned, and applied to
  output files without writing config by default;
- direct config writes remain gated by `--write-config` and
  `--confirm-reviewed-apply`;
- reviewed glossary additions for Telegram Bot API and OpenRouter were applied
  with a minimal `config/query_glossary.yaml` diff;
- Phase 4B smoke confirmed query enrichment and routing for the reviewed
  Telegram Bot API and OpenRouter questions.

## Current focus

Current active roadmap focus:

- Phase 5A - Service-aware Suggestions read-only MVP.
- Current branch: `phase5a-service-aware-suggestions`.
- Until Phase 5A is merged, do not start Supabase setup docs, MCP,
  docs health/stale refresh, or unrelated work unless the owner explicitly
  changes focus.

`docs/project_status.md` tracks project state and stable milestones, not an
exact latest-main pointer after every technical docs merge. Do not create
docs-only PRs only to update latest commit values or for cosmetic cleanup.

## Next recommended

- open a PR for `phase5a-service-aware-suggestions` after the branch is
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

## Later roadmap

Service-aware suggestions after Phase 5A:

- optional Phase 5B owner/admin Telegram UI for the read-only preview;
- keep ordinary user questions on the normal RAG path;
- do not auto-index from a normal user question.

Maintenance:

- refresh connected docs;
- disable docs source;
- docs source health report.

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
