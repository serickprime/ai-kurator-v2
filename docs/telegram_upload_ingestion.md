# Telegram Upload Ingestion

## How To Upload

1. Start the bot.
2. Press `Загрузить материал`.
3. Send a supported material file.
4. Wait for the upload result.
5. Press `Готово` when all files are uploaded.

Files sent outside upload mode are not indexed. The bot will ask you to press `Загрузить материал` first. This prevents accidental indexing of files that were meant only as question context.

## Supported Formats

The current loader supports:

- `.txt`
- `.md`, `.markdown`
- `.json`
- `.pdf`
- images: `.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`, `.tif`, `.tiff`

Image and PDF visual descriptions require vision mode and OpenRouter vision config. Text-layer PDF ingestion works without vision.

## What Happens After Upload

Telegram upload runs:

Telegram file -> local download -> loader/text extraction -> sections -> chunks -> document card -> embeddings -> Supabase rows.

Rows are written to:

- `documents`
- `document_cards`
- `sections`
- `chunks`

After a successful upload the bot replies with:

- document key;
- section count;
- chunk count;
- embedding status;
- term statistics status.

`Term statistics: updated` means `refresh_term_statistics()` ran. `missing fallback` means the material was saved, but the optional `term_statistics` table/RPC is absent and routing will use neutral term scoring until the schema is applied.

## Check Locally

From PowerShell:

```powershell
cd D:\Downloads\ai-kurator-v2
.\.venv\Scripts\python.exe scripts\smoke_telegram_upload_ingestion.py
```

Expected ready output:

```text
Upload ingestion: ready
document_id=...
sections=...
chunks=...
document_cards=...
embedding_dim=1024
term_statistics=updated
```

If configuration is missing, the smoke prints the missing variables and exits without starting Telegram polling.

## Verify Rows In Supabase

After upload, check that the document id from the bot or smoke has rows in:

```sql
select id, filename, status from documents order by created_at desc limit 5;
select document_id from document_cards order by created_at desc limit 5;
select document_id, heading from sections order by section_index asc limit 5;
select document_id, chunk_index, token_count from chunks order by chunk_index asc limit 5;
```

## term_statistics 404

If logs show `/rest/v1/term_statistics ... 404 Not Found`, the live Supabase schema is behind `app/db/schema.sql`.

Fix:

1. Apply `app/db/schema.sql` to the Supabase project.
2. Rebuild statistics:

```powershell
cd D:\Downloads\ai-kurator-v2
.\.venv\Scripts\python.exe scripts\rebuild_term_statistics.py --workspace team
```

The bot now has a fallback: missing `term_statistics` does not break ingestion or answering. It only disables corpus-aware term rarity until the table/RPC exists.
