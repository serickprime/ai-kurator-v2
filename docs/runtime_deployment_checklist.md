# Runtime Deployment Checklist

This checklist is for a local Windows PowerShell run or a small server run of the Telegram RAG v2 bot. It is intentionally operational: verify config, run read-only checks, start one bot process, and inspect logs.

## 1. Prepare The Environment

```powershell
cd D:\Downloads\ai-kurator-v2
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Create `.env` from `.env.example` and fill these values:

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
- `OLLAMA_BASE_URL` when `EMBEDDING_PROVIDER=local`
- `RAG_PIPELINE_VERSION=v2`

Keep `SUPABASE_SERVICE_ROLE_KEY`, `OPENROUTER_API_KEY`, and `TELEGRAM_BOT_TOKEN` server-side only. Do not paste real keys into docs, screenshots, issues, or logs.

## 2. Check Runtime Health

Run the read-only healthcheck first:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\runtime_healthcheck.py
```

The script does not start Telegram polling, does not send Telegram messages, does not index files, and does not write to Supabase.

Expected healthy shape:

```text
Runtime healthcheck: OK
[OK] Config: TELEGRAM_BOT_TOKEN - present
[OK] Supabase read-only check - workspace found: team
[OK] Service/docs status - n8n: indexed, PASS; Supabase: indexed, PASS
```

If it prints `FAIL`, fix the missing config or connectivity problem before starting the bot.

## 3. Supabase Checks

Apply `app/db/schema.sql` before first real use. Then run:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\smoke_supabase.py
```

Expected:

```text
Supabase OK: workspace=...
```

If `DEFAULT_WORKSPACE_ID` is not found, create or select the correct row in `workspaces` and update `.env`.

## 4. OpenRouter Checks

This smoke sends one tiny completion request:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\smoke_openrouter.py
```

Expected:

```text
OpenRouter OK: model=...
```

If it fails with HTTP 401 or 403, rotate/fix the OpenRouter key or model access. Do not print the key in support messages.

## 5. Ollama Embeddings Checks

For local embeddings, Ollama must be running and the configured model must exist.

```powershell
Invoke-RestMethod http://localhost:11434/api/tags
```

If the model is missing, pull it before ingesting materials:

```powershell
ollama pull BAAI/bge-m3
```

Then run the runtime builder smoke:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\smoke_rag_runtime.py
```

Expected:

```text
RAG runtime: ready
Pipeline: EvidenceFirstRagPipeline
```

## 6. Telegram Token And Polling Conflict

Check Telegram config without starting the bot:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\smoke_telegram_config.py
```

Only one process can use Telegram long polling for the same bot token. If startup logs show a polling conflict, stop the old process first:

```powershell
Get-Process python
```

Stop only the known old bot process. Do not run two local consoles and one server instance with the same token.

## 7. Start The Bot

For day-to-day local or small-server operation, use the runner script:

```powershell
cd D:\Downloads\ai-kurator-v2
.\.venv\Scripts\python.exe scripts\runtime_healthcheck.py
.\.venv\Scripts\python.exe scripts\run_telegram_bot.py
```

`scripts\run_telegram_bot.py` runs the same read-only healthcheck before startup. If healthcheck returns `FAIL`, it does not start polling. If healthcheck returns `WARN`, it prints the warning and continues.

The runner writes a local PID lock to `logs\telegram_bot.pid`. This prevents a second bot process started through the same runner from using the same Telegram polling token. It cannot reliably detect a bot already running on another server or one started manually through another entrypoint, so still stop known old processes before moving the bot between machines.

Healthy startup writes runtime logs to:

- `logs/app.log`
- `logs/errors.log`

Inspect logs:

```powershell
Get-Content logs\app.log -Tail 80
Get-Content logs\errors.log -Tail 80
```

Stop the bot with `Ctrl+C` in the PowerShell window where it is running. A normal stop prints:

```text
Telegram bot stopped by Ctrl+C.
```

If startup reports an existing local lock, check local Python processes and stop the known old bot process before retrying:

```powershell
Get-Process python
```

## 8. Telegram Runtime Acceptance

In Telegram, check:

- `/start` returns the main menu.
- `/help` shows question, upload, `/base_status`, `/services`, `/status`, `/new`, and `/debug_last`.
- `/base_status` shows document/chunk counts, external docs status, services, and recent documents.
- `/services` shows detected services and docs status.
- `Загрузить материал` enters upload mode.
- `Готово` exits upload mode.
- `/debug_last` shows the latest debug summary after a question.
- A normal greeting such as `привет` does not invent sources.
- A local-material question uses local evidence.
- An official-docs question uses external docs only when exact evidence is indexed.

## 9. Upload Acceptance

Use the `Загрузить материал` button or `/upload`, send a small `.txt` or `.md`, and wait for the final upload result.

Expected upload feedback:

- file received;
- processing started;
- document name;
- section count;
- chunk count;
- detected services or `сервисы не найдены`;
- next step: ask questions by the material.

For a non-Telegram local smoke that writes one tiny test material through the same ingestion service:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\smoke_telegram_upload_ingestion.py
```

## 10. Typical Errors

`RAG runtime: disabled`

Fix missing `.env` values printed by `scripts\smoke_rag_runtime.py` or `scripts\runtime_healthcheck.py`.

`DEFAULT_WORKSPACE_ID was not found`

The bot can reach Supabase, but `.env` points to a workspace UUID that is absent from `workspaces`.

`Telegram getMe failed`

Check `TELEGRAM_BOT_TOKEN`. If the token was exposed, rotate it in BotFather.

`Conflict: terminated by other getUpdates request`

Another bot process is polling with the same Telegram token. Stop the old local/server process.

`term_statistics ... 404`

Apply the current `app/db/schema.sql`. The bot has fallback behavior, but term rarity scoring works best after the table exists and is rebuilt.

`OpenRouter smoke failed`

Check `OPENROUTER_API_KEY`, model access, and `OPENROUTER_DEFAULT_MODEL`.

`Embedding dimension mismatch`

The database expects `vector(1024)`. Use a 1024-dimensional embedding model or plan a full schema/reindex change.

## 11. Pre-Change Verification

Before changing runtime code:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe -m compileall app scripts tests
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe -m pytest
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\check_tracked_secrets.py
```

These checks should pass before opening or merging a deployment-related PR.
