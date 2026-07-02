# Manual Smoke Checklist

Use this checklist after meaningful Telegram, RAG, docs registry, or runtime changes.

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

Status surfaces:

- `/docs`
- `/base_status`
- `/services`

Expected:

- if Telegram Bot API quality is `WARN` or `FAIL`, the user-facing status includes a short reason;
- do not show a bare `FAIL` without explanation.

## Forbidden smoke

Do not run these unless explicitly requested:

- `/docs_activate openrouter confirm`
- any crawl/sync/indexing command
- any activation confirm command
