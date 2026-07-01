# Run Telegram RAG V2

## Windows Setup

```powershell
cd D:\Downloads\ai-kurator-v2
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Create `.env` from `.env.example` and fill the required values:

- `TELEGRAM_BOT_TOKEN`
- `OWNER_IDS`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `DEFAULT_WORKSPACE_ID`
- `OPENROUTER_API_KEY`
- `OPENROUTER_DEFAULT_MODEL`
- `EMBEDDING_PROVIDER`
- `EMBEDDING_MODEL`
- `EMBEDDING_DIM`
- `OLLAMA_BASE_URL` when `EMBEDDING_PROVIDER=local`
- `RAG_PIPELINE_VERSION=v2`

The service role key is server-only. Do not put it into client-side code or public logs.

## Database And Materials

Apply `app/db/schema.sql` to the Supabase project.

Then ingest materials:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\ingest_files.py --path .\materials --workspace team --course "n8n 3.0"
```

After a large import or reindexing, rebuild term statistics:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\rebuild_term_statistics.py --workspace team
```

## Smoke Check

This command does not send Telegram messages and does not start polling:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\smoke_rag_runtime.py
```

If `.env` is missing or incomplete, it prints `RAG runtime: disabled` and lists missing variables instead of crashing.

## Start Bot

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe app\main.py
```

When configuration is complete and materials are indexed, Telegram answers go through `EvidenceFirstRagPipeline`.

If RAG configuration is incomplete, the bot still starts when `TELEGRAM_BOT_TOKEN` is present. On questions, it replies that RAG v2 is not connected and points to `.env`.
