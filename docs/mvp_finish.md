# MVP Finish

Last updated: 2026-07-16.

Scope boundary: this checklist tracks the docs candidate suggestions MVP. It is
not a production migration log, production crawl log, or release log.

## Progress

MVP progress: 14/15.

- [x] `docs_candidate_suggestions` migration exists with statuses, preview result, dedupe, RLS, and service-role access.
- [x] `app/db/schema.sql` mirrors the migration contract for the suggestions table, trigger, indexes, RLS, grants, and revokes.
- [x] Repository supports create, get by id, find by service URL, list pending/reviewable, status update, preview save, reject, activation result save, and deduplication.
- [x] Service layer can create or reuse a pending suggestion from an existing `config/docs_source_candidates.yaml` record.
- [x] Owner/admin `/docs_suggestions` list and card work for persisted pending and preview-ready suggestions.
- [x] Owner/admin authorization is enforced on the command and every suggestion callback.
- [x] Preview works from a persisted suggestion and saves compact `preview_result` plus `preview_status`.
- [x] Reject works with `rejected_by_owner`, reviewer metadata, and removal from the pending/review list.
- [x] Missing suggestions migration is handled without crashing Telegram runtime.
- [x] Documentation discovery settings are off by default and the HTTP search provider is config-gated.
- [x] Unknown-service detection runs at most one search and skips active docs, curated candidates, existing suggestions, ordinary words, URLs, emails, UUIDs, secret-like values, and random tokens.
- [x] Discovery result validation rejects unsafe URLs, private/local hosts, forbidden paths, forums/blogs/aggregators/issue trackers, redirects to unverified domains, and low-confidence results.
- [x] A valid official discovery result creates or reuses a pending suggestion and regular users receive a non-technical response.
- [x] Owner approval requires successful preview, explicit confirmation, existing activation service reuse, a one-suggestion dynamic URL/domain policy, and compact activation result persistence.
- [ ] Owner-approved migration apply, search provider configuration, one manual Telegram smoke, and commit/push/merge.

## Verified

- `.\.venv\Scripts\python.exe -m pytest tests\test_docs_discovery.py tests\test_telegram_docs_discovery.py tests\test_telegram_docs_suggestions.py tests\test_docs_candidate_suggestions.py tests\test_docs_activation.py`
- Result: 44 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\test_docs_candidate_preview.py tests\test_telegram_docs_preview.py tests\test_docs_activation.py tests\test_telegram_docs_activate.py tests\test_docs_activation_queue.py`
- Result: 39 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\test_telegram_docs_suggestions.py tests\test_telegram_docs_discovery.py tests\test_telegram_command_fallback.py`
- Result: 26 passed.

## Not Done

- No production migration apply.
- No production Supabase calls.
- No real web search API call.
- No real Telegram send.
- No real crawl, indexing, reindex, or activation run.
- No `.env` changes.
- No commit or push.
