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

## Forbidden smoke

Do not run these unless explicitly requested:

- `/docs_activate openrouter confirm`
- any crawl/sync/indexing command
- any activation confirm command
