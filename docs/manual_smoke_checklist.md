# Manual Smoke Checklist

Use this checklist after meaningful Telegram, RAG, docs registry, or runtime changes.

## Phase boundaries

Available now:

- project control doc checks;
- normal Telegram command smoke;
- knowledge-base/status commands;
- docs registry preview/status smoke;
- safe local no-write answer-quality harness from Phase 7C-A;
- targeted official-doc RAG smoke through the real Telegram bot when the owner
  explicitly chooses manual runtime testing.

Available through the Phase 7C-A harness:

- deterministic functional answer matrix without real Telegram messages;
- diagnostics for routing, selected documents, accepted evidence, citations,
  archived evidence exclusion, unsupported claims, and dirty documentation
  fragments;
- sanitized JSON artifact outside Git.

Planned after Phase 8B:

- genuine follow-up memory checks;
- list/switch/continue conversation flows;
- bounded message-history loading;
- user isolation checks.

Do not run activation confirm, crawl, sync, indexing, reindex, production
writes, or Telegram sends unless the owner explicitly asks for that operation.

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

## Phase 7C-A Functional Answer Matrix

Status: available. Phase 7C-A baseline is complete.

The harness uses the real QuestionAnalyzer, document router, evidence
retriever, reranker, evidence pack builder, AnswerGenerator, ClaimVerifier, and
source formatter. EvidenceLogRepository is disabled by constructing the
pipeline with `logger=None`. The harness sends no Telegram messages and wraps
Supabase in a read-only adapter. Production reads require explicit owner
approval.

Run pattern:

```powershell
.\.venv\Scripts\python.exe scripts\run_answer_quality_baseline.py --output <external-json-path> --resume --answer-mode cheap --confirm-read-only-production
```

The output path must be outside Git. Do not commit baseline JSON artifacts.

Completed baseline result:

- overall classification: `functional_blocker_found`;
- primary blocker: `evidence_selection_gap`;
- no Supabase write attempts, blocked write calls, or non-allowlisted RPC
  attempts were reported by the harness;
- `n8n_docs` and `openrouter_docs` were WARN because expected high-signal
  evidence terms were not accepted;
- `mixed_course_service_auto` was BLOCKED because no suitable uploaded
  material/service fixture was found;
- `vision_optional` was SKIPPED because no safe local test image was available;
- dirty documentation residue did not affect final answers or citations.

Baseline cases:

1. Course-material-only question.
2. Explicit Telegram Bot API question.
3. Explicit n8n question.
4. Explicit OpenRouter question.
5. Explicit Supabase question.
6. Mixed course task plus named service documentation.
7. Ambiguous service question.
8. Unsupported/out-of-base question.
9. Archived version exclusion.
10. Citation/source-label quality.
11. No internal IDs or debug data in final answer.
12. Follow-up-style question, recorded as unsupported or partial until Phase
    8B is implemented.
13. Optional text-plus-image case when vision runtime is available.

For every case evaluate:

- service/topic detection;
- course routing;
- documentation routing;
- selected documents;
- accepted evidence;
- archived evidence exclusion;
- answer completeness;
- unsupported claims;
- citations/source-label readability;
- insufficient-evidence behavior;
- whether dirty documentation fragments enter the answer.

Acceptance principles:

- answers must use accepted evidence only;
- source labels must come from accepted evidence;
- mixed course/docs questions should be able to use both course-material
  evidence and official documentation when both are relevant;
- the bot must state uncertainty or ask for clarification when evidence is
  insufficient;
- ordinary users must not see UUIDs, raw chunks, debug metadata, or internal
  implementation details.

## Phase 8B Conversation Memory smoke

Status: planned after Phase 8B; not available yet.

Future checks:

- "Новая тема" creates a new topic without leaking previous context.
- Previous conversations can be listed.
- A selected previous conversation can be reopened.
- A conversation can be continued with bounded recent context.
- Missing or deleted conversations are handled safely.
- User A cannot see or use User B history.
- Previous assistant answers are not treated as trusted evidence.
- Every follow-up still performs evidence-first retrieval from active uploaded
  materials and approved official documentation.
- Persistent user settings wiring is evaluated without turning settings into
  evidence.

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

## Service-aware Suggestions smoke

Run after Phase 5A service suggestion changes:

```powershell
.\.venv\Scripts\python.exe scripts\suggest_service_docs.py --help
.\.venv\Scripts\python.exe scripts\suggest_service_docs.py --question "как отправить сообщение через Telegram Bot API"
.\.venv\Scripts\python.exe scripts\suggest_service_docs.py --question "как подключить Stripe в n8n"
.\.venv\Scripts\python.exe scripts\suggest_service_docs.py --question "как работать с каким-то новым сервисом"
```

Expected:

- report mode is `read-only`;
- active supported services return `supported-active` and do not create an
  owner suggestion;
- missing or inactive known services show owner/admin review required;
- unknown services do not get a false high-confidence suggestion;
- auto activation is disabled;
- no Telegram UI is required for Phase 5A;
- `config/query_glossary.yaml` is not changed;
- Supabase is not written to;
- activation, crawl, sync, indexing, and reindex are not run.

## Service-aware Telegram Preview smoke

Run after Phase 5B service suggestion Telegram preview changes:

```text
/service_suggest РєР°Рє РѕС‚РїСЂР°РІРёС‚СЊ СЃРѕРѕР±С‰РµРЅРёРµ С‡РµСЂРµР· Telegram Bot API
/service_suggest РєР°Рє РїРѕРґРєР»СЋС‡РёС‚СЊ Stripe РІ n8n
/service_suggest РєР°Рє СЂР°Р±РѕС‚Р°С‚СЊ СЃ РєР°РєРёРј-С‚Рѕ РЅРѕРІС‹Рј СЃРµСЂРІРёСЃРѕРј
```

Expected:

- command is owner/admin-only;
- active supported services return `supported-active` and no owner action;
- missing or inactive known services show owner/admin review required;
- active context services, such as n8n, remain context and do not replace the
  missing service target;
- unknown services do not get a false high-confidence suggestion;
- auto activation is disabled;
- preview does not run `/docs_preview`, activation, crawl, sync, indexing, or
  reindex;
- ordinary user messages do not receive this technical preview.

## Docs Source Health smoke

Run after Phase 6A docs health/stale report changes:

```powershell
.\.venv\Scripts\python.exe scripts\report_docs_health.py --help
.\.venv\Scripts\python.exe scripts\report_docs_health.py
.\.venv\Scripts\python.exe scripts\report_docs_health.py --service openrouter
.\.venv\Scripts\python.exe scripts\report_docs_health.py --service telegram_bot_api
```

Expected:

- report mode is `read-only`;
- runtime unavailable is shown as not verified, not as source failure;
- registered source, service, active state, docs status, quality status,
  status reason, timestamps if available, stale state, document/chunk counts,
  owner-review need, and safe next action are visible;
- staleness is separate from operational `WARN` or `FAIL`;
- automatic refresh is disabled;
- Supabase is not written to;
- activation, crawl, sync, indexing, reindex, migrations, and direct docs status
  edits are not run.

## Docs Health Telegram Preview smoke

Run after Phase 6B docs health Telegram preview changes:

```text
/docs_health
/docs_health openrouter
/docs_health telegram_bot_api
```

Expected:

- command is owner/admin-only;
- summary shows total, healthy, warning, failed, stale, inactive, unknown, and
  runtime status;
- OpenRouter filter shows `warning`, `fresh`, and generator boilerplate reason;
- Telegram Bot API filter shows `failed`, `fresh`, and raw HTML/navigation
  quality reasons;
- inactive sources are not shown as healthy;
- runtime unavailable is shown without traceback;
- automatic refresh is disabled;
- unauthorized users do not receive the technical report;
- ordinary user messages still go to the normal RAG flow;
- command does not refresh, repair, activate, crawl, sync, index, reindex,
  edit docs status, run migrations, or write Supabase.

## Advisory Documentation Discovery smoke

Run only after the owner explicitly approves a real Telegram/search-provider
smoke and the feature is configured. This smoke may create one pending
suggestion, but it must not activate or index anything.

1. Ask one ordinary text question about a synthetic or genuinely unsupported
   service name that passes the configured discovery policy.
2. Confirm the normal RAG answer or honest insufficient-evidence response is
   sent first.
3. Confirm a non-technical discovery notice follows when a pending suggestion
   is created.
4. Repeat with the search provider unavailable and confirm the RAG answer still
   arrives without a discovery error leaking to the user.

Expected:

- the RAG pipeline is called exactly once with the original question;
- discovery never replaces the answer;
- regular users do not receive confidence values, URLs, internal ids, or owner
  diagnostics;
- only a pending owner-review suggestion may be created;
- no crawl, sync, indexing, reindex, activation, migration, or schema change is
  run by the smoke.

## Source Quality Cleanup smoke

Run after Phase 7A external docs cleaning/quality changes:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_external_docs_extractor.py tests\test_external_docs_validation.py -q
.\.venv\Scripts\python.exe scripts\runtime_healthcheck.py
```

Expected:

- generator/page-template boilerplate is removed from cleaned fixtures;
- API endpoints, method names, parameters, headers, code blocks, and safe inline
  HTML examples are preserved;
- raw page HTML, navigation/footer, and cookie chrome fixtures are removed;
- quality gate still fails truly dirty raw page HTML fixtures;
- quality gate does not fail only because of useful fenced or safe inline HTML
  examples;
- runtime health may still show OpenRouter WARN and Telegram Bot API FAIL until
  a separate owner-approved reprocessing block;
- Supabase is not written to;
- activation, crawl, sync, indexing, reindex, migrations, and direct docs status
  edits are not run.

## Docs Reprocessing Preparation smoke

Run after Phase 7B.0 safe reprocessing preparation tooling changes:

```powershell
.\.venv\Scripts\python.exe scripts\plan_docs_reprocessing.py --help
.\.venv\Scripts\python.exe scripts\plan_docs_reprocessing.py --service openrouter
.\.venv\Scripts\python.exe scripts\plan_docs_reprocessing.py --service telegram_bot_api
.\.venv\Scripts\python.exe scripts\plan_docs_reprocessing.py --service openrouter --format json
```

Expected:

- plan mode is `read-only`;
- exactly one service/source scope is resolved;
- output shows active documents, cards, sections, chunks, versions,
  fingerprints, duplicate active document keys, expected write scope, risks,
  readiness, and blockers/warnings;
- automatic execution, Supabase writes, and activation/reprocessing are
  disabled;
- manifest export uses an explicit local output path, atomic write, checksum,
  and no overwrite without `--force`;
- manifest verification catches checksum, required field, scope, relationship,
  rollback-capability, and obvious secret-field problems;
- `--compare-live` blocks execution when the live baseline drifts from the
  manifest;
- production backup files are not committed to Git;
- `/docs_activate`, activation, crawl, sync, indexing, reindex, migrations,
  rollback writes, direct status edits, and source reprocessing are not run.

## Docs Reconciliation Planning smoke

Run after Phase 7B.1b safe reconciliation planning changes:

```powershell
.\.venv\Scripts\python.exe scripts\plan_docs_reconciliation.py --help
.\.venv\Scripts\python.exe -m pytest tests\test_docs_reconciliation_plan.py -q
```

Expected:

- plan mode is `read-only`;
- input is one service/source scope plus a local discovered-key snapshot;
- common, newly discovered, active-missing, possible-superseded, ambiguous, and
  canonical-collision cases are classified without writes;
- missing from snapshot is not treated as automatic obsolete;
- possible superseded pages require owner review;
- review export is a local owner-review file, not an apply file;
- review export uses atomic write, checksum, no overwrite without `--force`,
  and no embeddings, chunks, secrets, or page content;
- production snapshots/review files are not committed to Git;
- no archive, delete, activation, crawl, sync, indexing, reindex, migration,
  direct status edit, term-statistics refresh, or Supabase write is run.

## Reviewed External Docs Archive Tooling smoke

Run after Phase 7B.1g-A reviewed external-doc archive tooling changes:

```powershell
.\.venv\Scripts\python.exe scripts\archive_reviewed_external_doc.py --help
.\.venv\Scripts\python.exe -m pytest tests\test_reviewed_external_doc_archive.py -q
```

Expected:

- preview is the default mode;
- one exact document id is required;
- reviewed reconciliation artifact and fresh rollback-capable backup are
  required before readiness can pass;
- `keep_active` and `needs_more_review` decisions block archive;
- successor validation blocks mismatched or inactive successors;
- exact target drift in id, key, source, workspace, status, version, hash, or
  ingestion signature blocks archive;
- future execution requires `--confirm-archive-one` and an exact confirmation
  phrase;
- fake execution updates exactly one document row and leaves successor, cards,
  sections, chunks, and embeddings untouched;
- term-statistics refresh is called only after a successful fake archive and
  refresh failure is reported as partial failure;
- production archive is not run during smoke;
- no crawl, activation, indexing, reindex, migration, direct status edit,
  rollback, arbitrary URL handling, or Supabase production write is run.

## Reviewed External Docs Key-Scoped Reprocessing Tooling smoke

Run after Phase 7B.1g-B reviewed exact-key reprocessing tooling changes:

```powershell
.\.venv\Scripts\python.exe scripts\reprocess_reviewed_external_docs.py --help
.\.venv\Scripts\python.exe -m pytest tests\test_reviewed_key_reprocessing.py -q
```

Expected:

- preview is the default mode;
- exact reviewed document ids are required and the target set is bounded;
- reviewed reconciliation artifact and fresh post-archive rollback-capable
  backup are required before readiness can pass;
- only `keep_active` reviewed decisions are accepted;
- `superseded_by`, `archive_candidate`, and `needs_more_review` block
  reprocessing;
- arbitrary URL input and full source crawl are absent/disabled;
- fake preview performs no fetch and no writes;
- fake execution fetches only selected exact URLs, uses the generic extractor
  and Phase 7A cleaner, preserves useful terms, creates new versions only for
  selected keys, and archives only their previous active versions;
- all-target validation blocks writes when any selected target fails before
  indexing;
- term-statistics refresh is called once after full fake success and refresh
  failure is reported as partial failure;
- production backup, production preview, production fetch, production
  reprocessing, rollback, crawl, activation, indexing, reindex, migration,
  direct status edit, and Supabase production writes are not run.

## Reviewed External Docs Canonical Relocation Tooling smoke

Run after Phase 7B.1g-C reviewed canonical relocation tooling changes:

```powershell
.\.venv\Scripts\python.exe scripts\relocate_reviewed_external_doc.py --help
.\.venv\Scripts\python.exe -m pytest tests\test_reviewed_canonical_relocation.py -q
```

Expected:

- preview is the default mode;
- one exact old document id is required;
- a dedicated canonical relocation review artifact and fresh rollback-capable
  backup are required before readiness can pass;
- new key and fetch URL come only from the reviewed artifact;
- arbitrary URL input, batch mode, and full source crawl are absent/disabled;
- collision checks block existing active, archived, failed, or foreign-scope
  new keys;
- fake preview performs no fetch and no writes;
- fake execution fetches only the reviewed new URL, uses the generic extractor
  and Phase 7A cleaner, preserves reviewed useful terms, creates the new
  key-local document, records lineage, and archives the exact old document only
  after the new active document exists;
- term-statistics refresh is called once after full fake success and refresh
  failure is reported as partial failure;
- production relocation artifact creation, production backup, production
  preview, production fetch, production relocation, rollback, crawl,
  activation, indexing, reindex, migration, direct status edit, and Supabase
  production writes are not run.

## Forbidden smoke

Do not run these unless explicitly requested:

- `/docs_activate openrouter confirm`
- `/docs_activate telegram_bot_api confirm`
- `/docs_activate_ready confirm`
- any crawl/sync/indexing command
- any activation confirm command
