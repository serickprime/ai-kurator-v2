# External Docs Registry v2

## Цель

External Docs Registry v2 должен позволить владельцу бота удобно подключать официальную документацию новых сервисов без ручного редактирования YAML под каждый сервис.

Главный поток:

```text
service detected -> docs missing -> docs candidate -> preview/dry-run -> owner approval -> pending index -> quality check -> activate
```

На этом этапе фиксируется архитектура и UX-план. Новый runtime-код, новые Telegram handlers, изменения RAG pipeline, AnswerGenerator, retrieval/router и Supabase RAG schema пока не делаются.

## Что Уже Есть

Текущая система уже закрывает безопасный foundation:

- `config/external_docs.yaml` задаёт whitelisted external docs sources: `n8n_docs`, `supabase_docs`.
- `config/service_docs_registry.yaml` задаёт известные сервисы, aliases и связь `service_id -> docs_source`.
- `ExternalDocsCrawler` скачивает только HTML-страницы из `allowed_domains`, `allow_patterns` и с учётом `deny_patterns`.
- `ExternalDocsIndexer` пишет official/external docs в существующие evidence-first таблицы `documents`, `document_cards`, `sections`, `chunks`.
- `ServiceDocsStatusProvider` читает статус сервисов и docs sources без crawl/sync.
- `/services` показывает найденные сервисы и статус документации.
- `/base_status` показывает counts базы, external docs sources, services и последние документы.
- Quality gate уже проверяет raw HTML, missing URLs, duplicate active versions, empty chunks, source labels и low-value chunks.

## Почему Нужен V2

Ограничения текущей схемы:

- новый сервис нужно добавлять руками в YAML;
- нет user-friendly Telegram UI для подготовки подключения official docs;
- нет явных pending/preview/dry-run состояний для нового source;
- сложно отличить "сервис найден в базе" от "официальная документация подключена";
- при добавлении всего в `handlers.py` Telegram layer быстро разрастётся;
- owner не видит заранее, какие домены, URL, лимиты и исключения будут использованы.

V2 нужен не для замены evidence-first RAG, а для безопасного управления источниками official docs вокруг уже работающего external docs foundation.

## Принципы

- Бот не индексирует произвольные сайты без подтверждения владельца.
- Разрешены только official/approved domains из candidate catalog или уже утверждённого source.
- Любой новый docs source сначала получает pending/candidate статус, а не active.
- Telegram UI должен быть простым и коротким.
- RAG pipeline не меняется.
- AnswerGenerator не меняется.
- Retrieval/router не меняются.
- Supabase RAG schema на первом этапе не меняется.
- External docs не смешиваются с uploaded materials в material-management командах.
- External docs нельзя архивировать обычными material/source командами.
- Crawler/indexer не вызываются напрямую из `handlers.py`.
- Любая activation требует quality gate перед тем, как docs source станет активным для ответов.

## UX Для Владельца

Главная команда:

```text
/docs
```

Пример ответа:

```text
Документация:

Подключено:
✅ n8n
✅ Supabase

Можно подключить:
➕ Claude Code
➕ OpenRouter
➕ Ollama
➕ Dokploy
➕ Telegram Bot API / aiogram

Проблемы:
⚠️ нет

Кнопки:
[Подключённые] [Можно подключить]
[Проверить статус] [Помощь]
```

Интерфейс не должен превращаться в админ-панель внутри чата. Для MVP достаточно dashboard, списка кандидатов и preview/dry-run.

## UX Подключения Docs

Сценарий подключения:

1. Пользователь задаёт вопрос про сервис, у которого нет подключённой official docs.
2. Бот отвечает по текущей базе, если есть evidence.
3. Дополнительно бот может предложить владельцу:

```text
Официальная документация Claude Code не подключена. Подготовить подключение?
```

4. Кнопки:

```text
[Подготовить docs] [Не сейчас]
```

5. Бот показывает preview:

- название сервиса;
- официальный домен;
- стартовый URL;
- лимит страниц;
- crawl depth;
- что будет индексироваться;
- какие URL/path будут исключены;
- ожидаемый risk level;
- что quality gate проверит перед activation.

6. Только owner/admin может нажать:

```text
[Подключить]
```

7. После подключения docs source проходит:

```text
ready_to_index -> indexing -> indexed_pending_quality -> active
```

Если quality gate падает, source остаётся `failed` или `pending_review`, а RAG не использует его как active official docs.

## Команды

Команды нужно разделять на user-friendly и служебные.

Основные целевые команды:

- `/docs` — read-only dashboard;
- `/docs_add` — простой мастер добавления;
- `/docs_status <service>` — статус сервиса/docs source;
- `/docs_refresh <service>` — обновить approved source;
- `/docs_disable <service>` — отключить source.

Не нужно реализовывать все команды сразу.

MVP для следующего блока:

- `/docs` read-only dashboard;
- candidates catalog;
- docs preview/dry-run.

Первый preview command:

- `/docs_preview <service>` читает только `config/docs_source_candidates.yaml`;
- не принимает произвольные URL;
- запускает только safe dry-run для URL и domains из curated candidate;
- ограничивает preview максимум 5 страницами;
- не индексирует документы;
- не пишет в Supabase;
- не меняет config;
- не активирует docs source.

Manual candidate QA report: [External Docs Candidate QA](external_docs_candidate_qa.md).

## Controlled Activation MVP

The activation flow is intentionally narrow:

- candidates must come from `config/docs_source_candidates.yaml`;
- single-service activation is still controlled by an explicit command;
- batch activation is available only through the docs activation queue;
- ready candidates are activated only after owner/admin confirmation;
- MVP batch activation allowlist is `openrouter`, `telegram_bot_api`;
- `needs_review`, `failed`, and `already_connected` candidates are skipped;
- `risk_level` must be `low`;
- arbitrary URLs are rejected.

Telegram flow:

```text
/docs_activate openrouter
```

This shows the activation plan only. It does not crawl, index, write to Supabase, or activate docs.

```text
/docs_activate openrouter confirm
```

This runs controlled activation for OpenRouter through the existing crawler/extractor/indexer path, limited by the curated candidate settings. The MVP uses the existing indexer behavior after preflight policy checks; it does not introduce a new pending schema state. If a future PR needs full pending activation, that should be handled as an explicit schema/design change.

Other candidates outside the MVP allowlist, including Ollama, Dokploy, aiogram, and Claude Code, remain non-activated until a separate controlled experiment approves them.

Batch queue commands:

- `/docs_preview_all` previews curated candidates and classifies them as `ready`, `needs_review`, `failed`, or `already_connected`.
- `/docs_ready` shows ready candidates that can be included in an activation plan.
- `/docs_activate_ready` shows a no-write plan.
- `/docs_activate_ready confirm` runs activation only for ready allowlisted candidates.

The queue commands do not accept arbitrary URLs. Preview and plan commands do not index documents, write to Supabase, or activate docs.

## Модули

Целевая структура:

```text
app/docs_registry/
  models.py
  repository.py
  service.py
  policy.py
  formatting.py
  candidates.py

app/bot/features/
  docs_registry.py
```

Границы:

- `handlers.py` только регистрирует команды и callback handlers;
- логика Telegram docs UI живёт в `app/bot/features/docs_registry.py`;
- бизнес-логика живёт в `app/docs_registry/service.py`;
- форматирование сообщений отдельно от бизнес-логики;
- candidate catalog читается через `app/docs_registry/candidates.py`;
- crawler/indexer вызываются только через service layer или отдельный approved workflow, а не напрямую из handlers;
- существующие `app/external_docs/*` остаются инфраструктурой crawl/extract/index/validate;
- существующий `app/service_registry/*` остаётся read-only источником статуса сервисов, пока v2 не заменит его постепенно.

## Статусы Docs Source

Целевая модель статусов:

- `not_configured` — сервис известен, docs source не настроен;
- `candidate` — есть безопасный кандидат из catalog;
- `suggested` — бот предложил владельцу подключение;
- `pending_review` — owner должен проверить preview;
- `ready_to_index` — approved и готов к dry-run/indexing;
- `indexing` — идёт индексирование;
- `indexed_pending_quality` — данные загружены, quality gate ещё не дал PASS;
- `active` — source прошёл quality gate и доступен RAG;
- `disabled` — source отключён владельцем;
- `failed` — crawl/index/quality завершились ошибкой.

MVP может использовать упрощённую модель:

- `active`
- `not_configured`
- `candidate`
- `failed`

Но в документе и данных лучше оставить путь к полной модели, чтобы не переделывать UX после первого кандидата.

## Candidates Catalog

Новый файл:

```text
config/docs_source_candidates.yaml
```

Назначение: безопасный каталог потенциальных official docs sources. Это не active whitelist. Кандидат не индексируется, пока owner/admin не подтвердит preview/dry-run.

Первый read-only catalog уже может отображаться в `/docs`, но это только список потенциальных sources. Наличие кандидата не означает, что documentation source подключён, активирован или будет использоваться RAG.

Первый список кандидатов:

- `claude_code` / Anthropic Claude Code
- `openrouter`
- `ollama`
- `codex_openai`
- `cursor`
- `dokploy`
- `telegram_bot_api`
- `aiogram`
- `gigachat`
- `proxyapi`
- `nocodb`
- `albato`

`FlutterFlow` не включается в первый приоритет.

Поля кандидата:

- `service_id`
- `display_name`
- `aliases`
- `docs_source`
- `official_start_urls`
- `allowed_domains`
- `allow_patterns`
- `deny_patterns`
- `max_pages`
- `crawl_depth`
- `risk_level`
- `notes`

Пример формы данных:

```yaml
candidates:
  - service_id: claude_code
    display_name: Anthropic Claude Code
    aliases:
      - claude code
      - claude cli
    docs_source: claude_code_docs
    official_start_urls:
      - https://docs.anthropic.com/en/docs/claude-code
    allowed_domains:
      - docs.anthropic.com
    allow_patterns:
      - "^https://docs\\.anthropic\\.com/en/docs/claude-code"
    deny_patterns:
      - "/login"
      - "/account"
    max_pages: 25
    crawl_depth: 2
    risk_level: medium
    notes: "Start with dry-run and quality gate before activation."
```

Правила для catalog:

- файл валидируется локальным loader без PyYAML и без новых зависимостей;
- `service_id` и `docs_source` должны быть уникальными;
- `official_start_urls` должны быть внутри `allowed_domains`;
- `allow_patterns` и `deny_patterns` должны быть валидными regex;
- `max_pages` должен быть больше `0`;
- `crawl_depth` должен быть `0` или больше;
- `risk_level` допускает только `low`, `medium`, `review`.

## Безопасность

Ограничения:

- never crawl arbitrary user URL;
- only candidate/approved official domains;
- deny login/account/admin/dashboard pages;
- limit max pages;
- dry-run before indexing;
- no secrets in Telegram;
- no `.env` changes from Telegram;
- no external docs sync from normal user messages;
- owner/admin only for activation;
- source activation must be auditable in logs;
- failed quality gate must not activate source automatically.

## Quality Gate

Перед activation нужно проверить:

- pages fetched > 0;
- chunks created > 0;
- no forbidden domains;
- source metadata present;
- source URLs/canonical URLs present;
- no raw HTML/nav/footer/cookie noise;
- no duplicate active versions;
- source labels include URLs;
- `/services` sees docs source;
- `/base_status` shows PASS;
- smoke question returns official source;
- `/source_last` marks source as official.

Если quality gate даёт WARN, source остаётся `indexed_pending_quality` или `pending_review` до ручного решения owner/admin.

Если quality gate даёт FAIL, source не активируется.

## Roadmap

Маленькие PR-блоки:

1. Docs Registry v2 architecture document.
2. Read-only `/docs` dashboard.
3. Candidates catalog.
4. Preview/dry-run for candidates.
5. Activation flow for one candidate.
6. Refresh/disable flow.
7. Auto-suggestion when user asks about service without docs.
8. Scheduled/manual refresh later.

## Что Не Делаем Сейчас

- Не подключаем Claude Code прямо сейчас.
- Не запускаем crawl.
- Не запускаем external docs sync.
- Не меняем Supabase schema.
- Не делаем auto-index from arbitrary URL.
- Не добавляем много команд в `handlers.py`.
- Не трогаем RAG pipeline.
- Не меняем AnswerGenerator.
- Не меняем retrieval/router.
- Не меняем `config/external_docs.yaml`.
- Не меняем `config/service_docs_registry.yaml`.
