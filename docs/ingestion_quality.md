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

## Reingest

These cleanup changes apply when materials are ingested. Existing rows in
Supabase keep their old text until the materials are reingested. Reingest old
materials when chunk previews or source labels still show loader scaffolding,
glued PDF text, or boilerplate headings.
