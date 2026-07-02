# Project Status

## Current main

Current main after the latest completed merge:

- `d101b74 Add docs activation queue (#19)`

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

## Current branch block

Telegram Bot API query quality fix implemented in branch `telegram-bot-api-query-quality-fix`:

- service-aware query enrichment for Telegram Bot API Russian send-message questions;
- `как отправить сообщение через Telegram Bot API?` adds retrieval anchors:
  - `sendMessage`
  - `chat_id`
  - `text`
- original user question is preserved;
- no reindex is needed because `sendMessage` evidence already exists;
- `/base_status`, `/docs`, and `/services` should show quality reasons instead of bare `FAIL`.

## Current focus

Review and test Telegram Bot API query quality bugfix.

## Next recommended

- manual Telegram smoke:
  - `Новая тема`
  - `как отправить сообщение через Telegram Bot API?`
  - `/source_last`
- verify the answer uses `telegram_bot_api_docs`;
- verify accepted evidence includes `sendMessage`, `chat_id`, and `text`;
- verify `/base_status` and `/docs` do not show unexplained bare `FAIL`.

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
