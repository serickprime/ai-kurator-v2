# Service Docs Registry

The service/docs registry is a small status layer between the local knowledge base and whitelisted official docs.
It tells the bot which services are known, which aliases identify them, which docs source is configured, and whether
that source is actually indexed and passing the external docs quality gate.

This layer does not crawl the internet and does not change RAG scoring. It only reports registry and indexing status.

## Why It Exists

The material base will keep growing and will mention many services. The bot should not decide on its own to browse the
web for every new service name. A service becomes eligible for official documentation only when it is added to
`config/service_docs_registry.yaml` and, if needed, connected to a whitelisted source in `config/external_docs.yaml`.

## Add A Service

Add a row to `config/service_docs_registry.yaml`:

```yaml
services:
  - service_id: example
    display_name: Example
    aliases:
      - example
      - пример
    docs_source: null
    status: not_configured
```

Use `status: not_configured` when official docs are not connected yet.

## Connect A Docs Source

1. Add the source to `config/external_docs.yaml`.
2. Set `docs_source` in `config/service_docs_registry.yaml` to that source name.
3. Keep `status: enabled` only after the source is approved for indexing.
4. Run the external docs quality workflow separately.

## Check Status

```powershell
cd D:\Downloads\ai-kurator-v2
.\.venv\Scripts\python.exe scripts\service_docs_status.py
.\.venv\Scripts\python.exe scripts\service_docs_status.py --json
.\.venv\Scripts\python.exe scripts\service_docs_status.py --scan-corpus
.\.venv\Scripts\python.exe scripts\service_docs_status.py --service n8n
```

The script reports:

- registry service id and aliases;
- configured docs source;
- active external docs and chunks count;
- quality gate status;
- optional mention count in indexed corpus rows;
- detected documents/chunks tagged by ingestion metadata when `--scan-corpus` is enabled;
- final docs status.

## Ingestion Discovery

When new local materials are ingested, the indexing pipeline loads `config/service_docs_registry.yaml`, detects service
aliases in the loaded document, sections, and chunks, and stores safe JSON metadata:

- `service_ids`;
- `service_mentions`.

No database schema change is required because the values are stored in existing `metadata` JSONB fields for documents,
document cards, sections, and chunks. Existing materials are not rewritten automatically; reingest is needed if old rows
should receive service metadata.

## Status Meanings

- `indexed`: docs source is configured, active docs exist, and quality is clean.
- `configured_not_indexed`: docs source is whitelisted but no active docs are indexed yet.
- `not_configured`: service is known, but no docs source is attached.
- `disabled`: registry entry is intentionally disabled and must not be used.
- `needs_review`: config or indexed quality needs human review before use.

## Telegram Use Later

The Telegram layer exposes `/services` as a compact read-only status command. It does not crawl docs and does not add
new sources. It shows whether a service is found in the indexed base, whether official docs are connected, and the
quality gate result when available.
