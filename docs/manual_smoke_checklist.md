# Manual Smoke Checklist

Use this checklist after meaningful Telegram, RAG, docs registry, or runtime changes.

## Project control docs

- Read `docs/project_handoff_context.md` before nontrivial work.
- Read `docs/prompting_playbook.md` before writing or changing prompts.
- Confirm GitHub repository: `serickprime/ai-kurator-v2`.
- Confirm local path: `D:\Downloads\ai-kurator-v2`.
- Confirm Supabase lookup starts from `app/db/schema.sql`, `app/db/repositories.py`, and read-only scripts.
- Confirm no `.env`, local credentials, service role keys, GitHub PATs, Telegram bot tokens, or secret-bearing logs are staged.
- Confirm the task does not fix one question point-wise when a general retrieval/query quality layer is needed.

## General Telegram

- `/start`
- `/help`
- `Новая тема`
- Ask a normal question.

Expected:

- bot answers;
- slash commands do not go to RAG;
- no raw traceback is shown to user.

## Knowledge base

- `/base_status`
- `/materials`
- `/source_last`

Expected:

- status commands work;
- material commands do not include external docs as uploaded materials;
- `/source_last` shows sources after a RAG answer.

## Docs Registry UI

- `/docs`
- button `Подключённые`
- button `Можно подключить`
- button `Проверить сервис`
- button `Помощь`
- `/services`

Expected:

- `/docs` shows dashboard;
- buttons respond visibly;
- connected docs are shown under connected docs;
- connected docs are not shown as candidates;
- buttons do not crawl, sync, index, or activate.

## Docs preview

- `/docs_preview openrouter`
- `/docs_preview https://example.com`

Expected:

- known candidate preview works;
- arbitrary URL is rejected;
- preview does not index documents;
- preview does not write to Supabase.

## Docs Activation Queue

- `/docs_preview_all`
- `/docs_ready`
- `/docs_activate_ready`
- `/docs_activate_ready https://example.com`

Expected:

- batch preview classifies candidates as ready, needs_review, failed, or already_connected;
- `/docs_preview_all` and the `Проверить всё` button show an immediate in-progress status before the final report;
- ready list shows only candidates eligible for the next activation plan;
- the `Готово к подключению` button uses the latest preview report; without one it asks to run `Проверить всё` or `/docs_preview_all` first;
- activation plan does not write to Supabase;
- arbitrary URL is rejected;
- do not run `/docs_activate_ready confirm` unless explicitly requested.

## OpenRouter RAG smoke

Run:

- `Новая тема`
- `как подключить openrouter api?`
- `/source_last`

Expected:

- answer uses OpenRouter official docs;
- answer does not show raw `Evidence:` labels or support quotes;
- `/source_last` shows:
  - source type: `external_docs`
  - source: `official`
  - source name: `openrouter_docs`

## Telegram Bot API RAG smoke

Run:

- `Новая тема`
- `как отправить сообщение через Telegram Bot API?`
- `/source_last`

Expected:

- answer uses Telegram Bot API official docs;
- `/source_last` shows:
  - source type: `external_docs`
  - source: `official`
  - source name: `telegram_bot_api_docs`
- accepted evidence should include:
  - `sendMessage`
  - `chat_id`
  - `text`
- answer should not fall back to a broad Bot API overview page without `sendMessage`.
- API parameter details should be readable in Telegram and should not depend on a wide markdown table rendering correctly.

Status surfaces:

- `/docs`
- `/base_status`
- `/services`

Expected:

- if Telegram Bot API quality is `WARN` or `FAIL`, the user-facing status includes a short reason;
- do not show a bare `FAIL` without explanation.

## Retrieval Query Quality smoke

Run after query enrichment changes:

- `Новая тема`
- `как отправить сообщение через Telegram Bot API?`
- `/source_last`
- `Новая тема`
- `как отправить запрос к api в n8n?`
- `/source_last`
- `Новая тема`
- `как подключить openrouter api ключ?`
- `/source_last`
- `Новая тема`
- `как сделать векторный поиск по документам в Supabase?`
- `/source_last`

Expected:

- Telegram Bot API answer uses `telegram_bot_api_docs` and evidence around `sendMessage`, `chat_id`, `text`;
- n8n answer uses official/local n8n evidence around `HTTP Request node`, `method`, `headers`, `body`;
- OpenRouter answer uses `openrouter_docs` and evidence around `API key`, `base_url`, `Authorization`, `Bearer`;
- Supabase answer uses `supabase_docs` when indexed evidence exists and should include `pgvector`, `match_documents`, or embeddings evidence;
- if exact evidence is not indexed, bot should return insufficient evidence rather than answer from a broad overview page.

Do not treat glossary anchors as answer content. The answer still needs accepted evidence.

## Glossary Candidate Discovery smoke

Run after Phase 4A read-only discovery changes:

```powershell
.\.venv\Scripts\python.exe scripts\suggest_query_glossary_candidates.py --help
.\.venv\Scripts\python.exe scripts\suggest_query_glossary_candidates.py --limit 10
```

Expected:

- report mode is `read-only`;
- candidates have `status: suggested`;
- output says candidates are not auto-applied;
- source refs are compact and do not print full chunks;
- `config/query_glossary.yaml` is not changed;
- Supabase is not written to;
- activation, crawl, sync, indexing, and reindex are not run.

## Glossary Candidate Review/Apply smoke

Run after Phase 4B CLI review/apply changes:

```powershell
.\.venv\Scripts\python.exe scripts\review_query_glossary_candidates.py --help
.\.venv\Scripts\python.exe scripts\review_query_glossary_candidates.py export-review --limit 10 --output reports\glossary_candidates_review.sample.yaml
.\.venv\Scripts\python.exe scripts\review_query_glossary_candidates.py validate-review --review-file reports\glossary_candidates_review.sample.yaml
.\.venv\Scripts\python.exe scripts\review_query_glossary_candidates.py plan-apply --review-file reports\glossary_candidates_review.sample.yaml
```

Expected:

- review file mode is `owner-review-required`;
- new candidates default to `owner_decision: pending`;
- pending and rejected candidates are not in the apply plan;
- sensitive-review candidates require `allow_sensitive_apply: true`;
- `plan-apply` is dry-run only;
- `apply-reviewed` is not run with `--write-config` unless the owner explicitly
  asks for direct config application;
- `config/query_glossary.yaml` is not changed by export, validate, or plan;
- Supabase is not written to;
- activation, crawl, sync, indexing, and reindex are not run.

## Forbidden smoke

Do not run these unless explicitly requested:

- `/docs_activate openrouter confirm`
- any crawl/sync/indexing command
- any activation confirm command
