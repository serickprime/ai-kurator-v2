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

## Latest completed project block

Project handoff context was merged in PR #21:

- `docs/project_handoff_context.md` is the first-stop context for future agents;
- `docs/prompting_playbook.md` defines how future prompts should describe scope, guardrails, checks, and reports;
- final reports now require a `Recommended next prompt` block with rationale,
  guardrails, and a copy-paste prompt, while making clear that the next block
  must not start without explicit owner instruction;
- project identity is documented:
  - GitHub repository: `serickprime/ai-kurator-v2`;
  - local path: `D:\Downloads\ai-kurator-v2`;
- Supabase data/table lookup rules are documented;
- git workflow, push, PR, and merge rules are documented;
- secrets policy is documented: secrets are not stored in the repository;
- retrieval/query quality principle is documented:
  - do not fix one user question point-wise;
  - build a general retrieval/query quality layer for uploaded materials and official docs.

## Current focus

Project handoff/status docs are in `main`. No next roadmap item is in progress.
`docs/project_status.md` tracks project state and stable milestones, not an
exact latest-main pointer after every technical docs merge.

## Next recommended

- keep using `docs/project_handoff_context.md` before nontrivial work;
- use `docs/prompting_playbook.md` before writing new prompts;
- keep building retrieval/query quality as a general layer, not as one-off fixes;
- keep the Retrieval Query Quality smoke below as a regression check for PR #20.

- manual Telegram smoke:
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

## Later roadmap

Service-aware suggestions:

- detect service in user question;
- if docs are missing and candidate exists, suggest preview;
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
