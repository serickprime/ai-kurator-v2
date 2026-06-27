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
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill `.env` with local secrets. Never commit `.env`.

`SUPABASE_SERVICE_ROLE_KEY` is only for the server-side Telegram bot and local maintenance scripts. Never put it in browser, mobile, frontend, README examples with real values, logs, or any client-side code.

## Run

```powershell
.\.venv\Scripts\python.exe -m app.main
```

## Telegram UX

The bot has a compact persistent reply keyboard:

- `Новая тема`
- `Загрузить материал`
- `Настройки`

Questions, captions, and image context are combined into one user intake before RAG. Vision text is context only, not a standalone question. Upload mode is explicit: files outside `Загрузить материал` are not indexed automatically, and text sent during upload mode does not go to RAG.

Answer model routing is controlled by per-user settings and the `OPENROUTER_*_MODELS` environment lists. Free mode never silently falls back to paid models. Quality can fall back to cheap only when `ALLOW_QUALITY_TO_CHEAP_FALLBACK=true`.

Persistent settings need the optional `user_settings` migration proposed in [Telegram UX](docs/bot_ux.md). It has not been applied automatically in this step.

## Ingest Materials

```powershell
.\.venv\Scripts\python.exe scripts\ingest_files.py --path .\materials --workspace team --course "n8n 3.0"
```

Ingestion creates a document row, a document card for document-first routing, parent sections, child chunks, and embeddings for the card, sections, and chunks. If a file has the same content hash as the active version, it is skipped. If the file changed, the old active document is archived after the new version is fully indexed.

Make sure `EMBEDDING_MODEL` points to a local model that actually returns 1024-dimensional vectors. The database schema uses `vector(1024)`, so older 768-dimensional models such as `nomic-embed-text` require a schema change or reindexing plan before use.

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
