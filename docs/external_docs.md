# External Docs

External docs are whitelisted official documentation pages indexed into the
same evidence-first RAG store as local course materials.

They are not live web answers. The bot must never answer directly from raw HTML,
crawler candidates, or arbitrary internet pages. A page can support an answer
only after it is fetched from an allowed source, extracted to clean structured
text, embedded, stored in Supabase, retrieved as evidence, accepted into an
`EvidencePack`, and used by the normal answer generator.

## Local-First

Local course materials remain the primary source. External docs are allowed only
when local evidence is missing, partial, or the question explicitly asks for
current or official documentation.

If local evidence is sufficient, external docs should not override it.

## Whitelist

Sources are configured in:

```powershell
config\external_docs.yaml
```

Each source defines:

- `name`
- `allowed_domains`
- `start_urls`
- `allow_patterns`
- `deny_patterns`
- `crawl_depth`
- `max_pages`
- `refresh_days`

New sources should be added through YAML, not Python code.

## Sync

Run one source:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\external_docs_sync.py --source n8n_docs --limit 20
```

Run all configured sources:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\external_docs_sync.py --all --limit 20
```

The sync command:

- loads the whitelist config;
- crawls only allowed domains and allowed URL patterns;
- skips denied, login, account, admin, user, and binary URLs;
- extracts clean structured text from HTML;
- preserves code blocks;
- stores pages as `source_type=external_docs`;
- writes external metadata into `documents`, `document_cards`, `sections`, and `chunks`;
- archives old active versions when a canonical URL changes;
- skips unchanged pages by `content_hash`.

## Status

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\external_docs_status.py
```

The status command shows configured sources, indexed document counts, active and
archived counts, latest crawl time, latest content hash, and warnings for empty
sources.

## Automated Quality Gate

Use the validator after every controlled sync. It reads already indexed active
external docs from Supabase and does not crawl new pages.

Validate one source:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\external_docs_validate.py --source n8n_docs --sample 10
```

Validate all configured sources:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\external_docs_validate.py --all --sample 10
```

Machine-readable output:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\external_docs_validate.py --source n8n_docs --json
```

The gate reports `QUALITY: PASS`, `QUALITY: WARN`, or `QUALITY: FAIL`.

`FAIL` means the source should not be expanded until the extractor/config is
fixed. Failures include raw HTML in chunks, active docs without
`source_url`/`canonical_url`, duplicate active document keys/canonical URLs,
empty chunks, or source labels without URLs.

`WARN` means the source can be reviewed with the printed samples before
expanding. Warnings include high title-only chunk ratio, high very-short chunk
ratio, high archived/active ratio, navigation/footer/generator markers,
suspicious huge chunks, or zero preserved code blocks for a technical docs
source.

Recommended workflow for every new source:

1. Add the source to `config\external_docs.yaml`.
2. Run a small sync, for example `--limit 10`.
3. Run `scripts\external_docs_validate.py --source <source_name> --sample 10`.
4. If `PASS`, expand to `--limit 25` or `--limit 50`.
5. If `WARN`, review the samples before expanding.
6. If `FAIL`, fix the extractor or whitelist config before expanding.

## Metadata

External docs are stored in existing tables using metadata:

- `source_kind=external_docs`
- `source_name`
- `source_domain`
- `source_url`
- `canonical_url`
- `crawled_at`
- `content_hash`
- `freshness_status`
- `external_docs_version`
- `content_type=[official_docs, external_docs]`

The stable `document_key` is the canonical URL.

## RAG Flow

The intended flow is:

```text
question
-> question analysis
-> local document routing and local evidence retrieval
-> if local evidence is sufficient, answer from local evidence
-> if local evidence is missing or current official docs are required, sync/index whitelisted external docs
-> retrieve evidence from indexed docs
-> build EvidencePack
-> answer only from EvidencePack
```

The first implementation provides sync/status scripts and stores external docs
in the same evidence tables. It does not add live web search inside Telegram or
inside `AnswerGenerator`.

## Updating Or Removing Docs

To update external docs, rerun sync. Unchanged pages are skipped. Changed pages
create a new active document version and archive the old one.

To remove old docs, prefer an explicit archive operation in code or SQL reviewed
for the exact source/document key. Do not manually delete broad chunks.
