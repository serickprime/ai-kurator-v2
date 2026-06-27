# Ported Utilities

This note records the safe parts reviewed from the old `bot_rag_kurator` project and how they were adapted for RAG v2.

## Reused

- `services/telegram_format.py`
  - Ported as `app.bot.formatting.format_for_telegram`.
  - Keeps code blocks copyable, escapes Telegram HTML, removes decorative Markdown artifacts, and fixes a few common Russian wording glitches.
  - Applied only at the Telegram output boundary.

- `services/file_loader.py`
  - Adapted into `app.ingestion.loaders`.
  - Added invalid JSON fallback, `json_valid` metadata, `looks_like_n8n_workflow`, `detect_file_type`, and `is_image`.

- `services/document_textifier.py`
  - Adapted into `app.ingestion.loaders` as optional PyMuPDF PDF layout extraction.
  - Keeps page markers, reads text blocks in layout order, and describes only significant image blocks when vision is enabled.
  - PyMuPDF remains optional; pypdf fallback still works.

- `services/openrouter_client.py`
  - Adapted into `app.llm.openrouter_client`.
  - Added typed OpenRouter errors, model listing, free-model filtering, optional `HTTP-Referer`/`X-Title` headers, and service-artifact detection.

- `services/user_access.py`
  - Adapted as pure Telegram-side policy in `app.bot.access`.
  - Does not introduce synchronous Supabase calls.

- `services/text_routing.py`
  - Adapted as `app.bot.text_routing` for short intake notes.
  - This is not part of RAG routing and does not alter document routing or evidence selection.

- `docs/eval_cases.json`
  - A few cases were adapted into the v2 eval schema, especially numeric-zero conditions, safe `match_documents`, multimodal intake, HTTP 401, and admin no-guessing.

## Not Reused

- `services/rag.py`
- old source relevance gates;
- old prompt builder;
- old `append_sources` logic;
- hardcoded answer guardrail blocks;
- old chunk-first retrieval flow.

RAG v2 still follows:

```text
question
-> question analysis
-> document router
-> evidence pack
-> answer generation
-> claim verifier
```
