# AI Kurator V2

Evidence-first Telegram RAG bot with Supabase, document-first retrieval, evidence packs, and claim verification.

## Why V2 Exists

The old chunk-first flow searched similar chunks across the full knowledge base and then sent mixed candidate chunks to the answer model. That made the bot vulnerable to noisy sources from unrelated lessons that happened to share broad terms like `n8n`, `Supabase`, `API`, or `Docker`.

V2 uses an evidence-first architecture:

- document-first retrieval selects the most relevant documents before detailed evidence search;
- evidence retrieval runs inside selected documents, not across every raw chunk in the database;
- the generation prompt receives only the evidence pack;
- answers must be written only from evidence;
- claim verification checks the draft against the evidence pack;
- sources are built only from evidence actually used in the answer;
- raw candidate chunks are never included in the generation prompt.

If no evidence is found, the bot should say that the answer is not supported by the knowledge base and ask for the missing material or a more specific question.

## Pipeline

```text
question
-> question analysis
-> document router
-> evidence retrieval inside selected documents
-> reranking
-> evidence pack
-> answer generation from evidence only
-> claim verification
-> final answer with sources from used evidence only
```

## Project Layout

```text
app/
  main.py
  config.py
  logging_config.py
  bot/
  db/
  ingestion/
  rag/
  llm/
  eval/
scripts/
tests/
docs/
```

## Requirements

- Python 3.11+
- Telegram bot token
- Supabase project with PostgreSQL and pgvector
- Local multilingual embeddings, defaulting to `BAAI/bge-m3` with `EMBEDDING_DIM=1024`
- OpenRouter-compatible chat model for answer generation

## Local Setup

```powershell
python -m venv .venv
pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill `.env` with local secrets. Never commit `.env`.

`SUPABASE_SERVICE_ROLE_KEY` is only for the server-side Telegram bot and local maintenance scripts. Never put it in browser, mobile, frontend, README examples with real values, logs, or any client-side code.

Required first-run settings:

- `TELEGRAM_BOT_TOKEN`
- `OWNER_IDS`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `DEFAULT_WORKSPACE_ID`
- `OPENROUTER_API_KEY`
- `OPENROUTER_DEFAULT_MODEL`
- `OPENROUTER_VISION_MODEL`
- `EMBEDDING_PROVIDER`
- `EMBEDDING_MODEL`
- `EMBEDDING_DIM=1024`
- `RAG_PIPELINE_VERSION=v2`

## Run

```powershell
python scripts/run_telegram_bot.py
```

Runtime logs are written to `logs/app.log` and `logs/errors.log`.
For the full Telegram RAG v2 runtime checklist on Windows, see [Run Telegram RAG V2](docs/run_telegram_rag.md).
For local/server startup verification, use the practical [Runtime Deployment Checklist](docs/runtime_deployment_checklist.md).
`scripts/run_telegram_bot.py` runs the read-only healthcheck first and uses a local PID lock to avoid starting a second runner-managed polling process.

## Smoke Checks

Run these after filling `.env` and applying the Supabase schema:

```powershell
python scripts/runtime_healthcheck.py
python scripts/smoke_telegram_config.py
python scripts/smoke_supabase.py
python scripts/smoke_openrouter.py
python scripts/smoke_rag_runtime.py
python scripts/smoke_telegram_upload_ingestion.py
```

`runtime_healthcheck.py` is read-only: it checks required config, the default Supabase workspace, and service/docs status without starting Telegram polling or writing to Supabase.
`smoke_supabase.py` is read-only and checks that `DEFAULT_WORKSPACE_ID` exists in `workspaces`.
`smoke_openrouter.py` sends a tiny completion request to `OPENROUTER_DEFAULT_MODEL`.
`smoke_rag_runtime.py` only builds runtime dependencies and prints missing `.env` settings when RAG v2 is disabled.
`smoke_telegram_upload_ingestion.py` writes a tiny txt material through the same ingestion service used by Telegram upload mode.

## Telegram UX

The bot has a compact persistent reply keyboard:

- `Новая тема`
- `Загрузить материал`
- `Настройки`

Questions, captions, and image context are combined into one user intake before RAG. Vision text is context only, not a standalone question. Upload mode is explicit: files outside `Загрузить материал` are not indexed automatically, and text sent during upload mode does not go to RAG.

Telegram material uploads are described in [Telegram Upload Ingestion](docs/telegram_upload_ingestion.md). Upload feedback shows processing status, document/section/chunk counts, and detected services when service discovery finds them.

Manual RAG quality smoke suite: [docs/rag_quality_smoke_suite.md](docs/rag_quality_smoke_suite.md).

External Docs Registry v2 design: [docs/external_docs_registry_v2.md](docs/external_docs_registry_v2.md).

Useful read-only Telegram status commands:

- `/services` shows detected services and whether their docs source is connected.
- `/base_status` shows knowledge base counts, external docs status, service status, and recent documents.
- `/materials` lists recent uploaded/local materials and excludes external docs.
- `/material <id>` shows one uploaded/local material card by full UUID or short displayed id.
- `/source_last` shows sources used by the last RAG answer.

Owner/admin material management:

- `/archive_material <id>` archives one active uploaded/local material by setting `documents.status = archived`.
- `/archive_source <id>` archives an uploaded/local source from the last RAG answer.

Archiving does not physically delete chunks and cannot be used for external/official docs.

When an answer used a bad uploaded/local source, check `/source_last`, archive the source with `/archive_source <id>`, then ask the question again.

Answer model routing is controlled by per-user settings and the `OPENROUTER_*_MODELS` environment lists. Free mode never silently falls back to paid models. Quality can fall back to cheap only when `ALLOW_QUALITY_TO_CHEAP_FALLBACK=true`.

`OPENROUTER_BASE_URL`, `OPENROUTER_SITE_URL`, and `OPENROUTER_APP_NAME` are optional transport/header settings. Keep `OPENROUTER_API_KEY` server-side only.

Persistent settings need the optional `user_settings` migration proposed in [Telegram UX](docs/bot_ux.md). It has not been applied automatically in this step.

## Ingest Materials

```powershell
.\.venv\Scripts\python.exe scripts\ingest_files.py --path .\materials --workspace team --course "n8n 3.0"
```

Ingestion creates a document row, a document card for document-first routing, parent sections, child chunks, and embeddings for the card, sections, and chunks. If a file has the same content hash as the active version, it is skipped. If the file changed, the old active document is archived after the new version is fully indexed.

Ingestion also attempts to refresh corpus term statistics, so frequently repeated terms automatically become weaker retrieval signals and rare exact terms become stronger anchors. After a large import or reindexing, rebuild them explicitly:

```powershell
.\.venv\Scripts\python.exe scripts\rebuild_term_statistics.py --workspace team
```

Make sure `EMBEDDING_MODEL` points to a local model that actually returns 1024-dimensional vectors. The database schema uses `vector(1024)`, so older 768-dimensional models such as `nomic-embed-text` require a schema change or reindexing plan before use.

## Demo Materials

The repository includes a tiny first-run corpus:

```powershell
python scripts/ingest_files.py --path .\sample_materials --workspace team --course "demo"
python scripts/evaluate.py --cases app\eval\cases.json --save-report
```

The sample materials cover `n8n local install`, `YooMoney setup`, and `Supabase match_documents` so routing can be checked against documents with overlapping technical terms.

## Simple Retrieval Benchmark

For retrieval-only diagnostics on non-technical household materials:

```powershell
python scripts/evaluate_retrieval_simple_synthetic.py
python scripts/evaluate_retrieval_simple_synthetic.py --reingest
python scripts/evaluate_retrieval_simple_synthetic.py --question "как поливать комнатный лимон зимой?"
```

Reports are written to `eval_runs/retrieval_simple_synthetic/latest.json` and `eval_runs/retrieval_simple_synthetic/latest.md`. This benchmark does not generate final answers; it checks document routing, evidence chunks, fact ids, and forbidden document leakage in the evidence pack.

## CI

GitHub Actions runs:

- `compileall` for `app`, `scripts`, and `tests`;
- `pytest`;
- JSON validation for committed `.json` files;
- a secret grep that fails on common Telegram, GitHub, and Supabase secret patterns.

## Как проверять качество

RAG v2 eval проверяет не только текст ответа, но и document routing, evidence, sources, answer mode и утечки forbidden/discarded candidates.

```powershell
.\.venv\Scripts\python.exe scripts\evaluate.py --cases app\eval\cases.json --save-report
```

Отчёты пишутся в `eval_runs/latest.json`, `eval_runs/latest.md` и timestamp-файлы. Без подключенного prediction-файла раннер честно помечает кейсы как `not_run`; реальные результаты pipeline можно подать через `--predictions path\to\predictions.json`.

Сравнение прогонов:

```powershell
.\.venv\Scripts\python.exe scripts\compare_eval_runs.py eval_runs\baseline.json eval_runs\latest.json
```

Регрессиями считаются пропавший expected document, появившийся forbidden document, лишние sources, неправильный answer mode, sources при `ask_for_missing_data`, использование discarded candidate и падение score на `0.5+`.

## Docs

- [Architecture](docs/architecture.md)
- [RAG pipeline](docs/rag_pipeline.md)
- [Evaluation](docs/eval.md)
- [Prompts](docs/prompts.md)
- [Telegram UX](docs/bot_ux.md)
- [Ported utilities](docs/ported_utilities.md)
