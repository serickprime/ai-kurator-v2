# Architecture

AI Kurator V2 separates retrieval candidates from generation evidence.

The system may cast a wide net during early retrieval, but the answer model receives only a compact evidence pack. This prevents generic shared terms from dragging unrelated lessons into the final answer.

## Main Components

- Telegram bot: receives questions and files, returns conversational answers.
- Ingestion: loads materials, extracts text, splits text into indexed units, and creates document cards.
- Supabase: stores documents, versions, document cards, indexed units, conversations, and messages.
- Question analysis: extracts routing signals from the user question.
- Document router: selects likely documents before detailed evidence retrieval.
- Evidence retriever: searches inside selected documents only.
- Evidence pack builder: keeps only compact, relevant, sourceable spans for generation.
- Answer generator: writes an answer only from the evidence pack.
- Claim verifier: checks the answer draft against the evidence pack.
- Source formatter: shows sources only from evidence used in the final answer.
- Smoke scripts: validate Telegram config, Supabase connectivity, and OpenRouter completion before the first real launch.
- CI: compiles Python, runs tests, validates JSON, and scans committed files for common secret patterns.

## Runtime Boundary

The Telegram bot is the only interactive runtime in the first release. It loads settings from `.env`, configures logs under `logs/`, and starts polling through `app/main.py`.

Server-side secrets are used only by local scripts and the bot process:

- `SUPABASE_SERVICE_ROLE_KEY` for Supabase service-role REST calls;
- `OPENROUTER_API_KEY` for answer and vision models;
- `TELEGRAM_BOT_TOKEN` for Telegram polling.

These values must never appear in frontend code, committed examples, reports, or logs.

## Non-Goals For The Runnable Structure

- No copied legacy `services/rag.py`.
- No global raw chunk context in generation prompts.
- No answer synthesis when evidence is missing.
- No schema changes are made by smoke scripts.

## Data Boundary

Chunks or indexed units can exist as internal retrieval data. They are not the generation contract.

The generation contract is:

```text
question + evidence pack -> answer draft
```

The source contract is:

```text
used evidence -> displayed sources
```

## Demo Corpus

`sample_materials/` contains a deliberately small corpus for first-run checks:

- `n8n_local_install.md`
- `yoomoney_setup.md`
- `supabase_match_documents.md`

These files share broad technical terms but answer different questions. They are meant to catch regressions where a generic platform match outranks an answerable document.
