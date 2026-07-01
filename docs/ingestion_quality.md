# Ingestion Quality

Ingestion should clean source material before chunking. The goal is to store
useful lesson text, not loader scaffolding or PDF extraction artifacts.

## Text Normalization

`TextNormalizer` runs before sectioning and chunking. It:

- normalizes line endings and extra spaces;
- joins wrapped prose lines;
- keeps fenced code blocks intact;
- keeps URLs, `.env`, `CLAUDE.md`, JSON/YAML-like lines, commands, and paths intact;
- removes boilerplate lines such as `Source file:`, `Название файла:`, and `Прочее`;
- repairs suspicious long Cyrillic-only glued tokens when they can be split into common words.

PDF text extracted with PyMuPDF joins spans with spacing heuristics before the
normalizer runs, so adjacent words from different spans are not glued together.

## Title Selection

For TXT and Markdown files, titles are selected only from meaningful Markdown
headings. Boilerplate headings are skipped:

- `Название файла:`
- `Прочее`
- `unknown`
- page markers such as `Page 1`

If no meaningful heading exists, the filename stem is used.

## Source Labels

Source labels are built with `SourceLabelBuilder`. It cleans raw titles and
locators, removes file extensions, deduplicates identical labels, and limits
labels from the same document to avoid repeating one file many times.

Sources still come only from accepted evidence in the `EvidencePack`.

## Inspect A Document

Run:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\inspect_ingested_document.py --filename CLn02_text_double_deep.txt
```

The script shows:

- document title and clean label;
- section and chunk counts;
- first section headings;
- first chunk previews;
- bad signs such as boilerplate title, boilerplate headings, duplicate source labels,
  suspicious glued Cyrillic text, and `Source file:` leakage into chunks.

## Reingest old materials

These cleanup changes apply when materials are ingested. Existing rows in
Supabase keep their old text until the materials are reingested. Reingest old
materials when chunk previews or source labels still show loader scaffolding,
glued PDF text, or boilerplate headings.

Reingest is safe for the same source file/document key:

- the current active document is detected by `workspace_id + document_key`;
- if the raw file hash and ingestion signature both match, the file is skipped;
- if the text cleanup output changed, a new document version is indexed;
- the old active document version is archived before the new version is activated;
- document router and chunk RPC functions only search `documents.status = 'active'`;
- term statistics are refreshed from active documents after successful ingestion.

Old archived rows are kept for history, so old materials do not clean themselves
automatically. To verify a specific file before and after reingest, run:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\inspect_ingested_document.py --filename CLn02_text_double_deep.txt
```

After reingest, check that `bad_signs.source_file_leaked_into_chunk` is `false`,
the title is not a boilerplate label, and the active chunk previews contain the
new cleaned text.
