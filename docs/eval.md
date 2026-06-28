# Evaluation

The RAG v2 evaluation suite checks the whole evidence-first path:

- document routing precision;
- forbidden document leakage;
- chunk/evidence relevance;
- source precision;
- answer mode;
- answer term coverage;
- claim grounding;
- discarded candidate leakage.

## Cases

Eval cases live in `app/eval/cases.json`. Each case has:

- `id`
- `category`
- `question`
- `expected_documents`
- `forbidden_documents`
- `expected_answer_terms`
- `forbidden_answer_terms`
- `expected_answer_mode`
- `expected_source_count_max`
- `must_not_use_discarded_candidates`
- `expected_supported_points`
- `requires_sources`

The starter set intentionally includes adversarial overlap such as `n8n`, `Supabase`, `API`, `Docker`, credentials, and payment terms. The goal is to catch cases where broad retrieval candidates are acceptable, but answer generation or sources leak unrelated lessons.

The first-run demo cases use `sample_materials/`:

- `sample_n8n_local_install`
- `sample_yoomoney_hash`
- `sample_supabase_match_documents`

They should be used after ingesting the sample corpus to confirm that document routing separates superficially similar technical materials.

## Running

```powershell
python scripts/evaluate.py --cases app/eval/cases.json --save-report
```

Without a predictions file, the runner produces a `not_run` report. This is deliberate: the eval layer should not invent RAG outputs when no live pipeline result was supplied.

## Simple Synthetic Retrieval Benchmark

Use this benchmark before judging answer generation. It uses simple household materials plus generated crowded IT distractors and checks only retrieval stages:

```powershell
python scripts/evaluate_retrieval_simple_synthetic.py
python scripts/evaluate_retrieval_simple_synthetic.py --reingest
python scripts/evaluate_retrieval_simple_synthetic.py --question "как поливать комнатный лимон зимой?"
```

Inputs:

- `sample_materials/rag_search_simple_test/`
- `app/eval/rag_search_simple_test_cases.json`

Reports:

- `eval_runs/retrieval_simple_synthetic/latest.json`
- `eval_runs/retrieval_simple_synthetic/latest.md`
- timestamped JSON and Markdown files in the same directory.

The benchmark shows document candidates, selected evidence chunks, found `FACT-ID`s, discarded candidates, and whether forbidden documents leaked into the evidence pack. Raw candidates may include similar forbidden documents; the failure condition is forbidden evidence in `evidence_pack`.

The generated crowded IT cases intentionally add many documents that repeat terms like `webhook`, `n8n`, and `Supabase`. The expected behavior is that frequent terms become weak signals while rare anchors such as `sha1_hash`, `localhost:5678`, and `match_documents` dominate document and evidence selection.

To evaluate real outputs, pass a JSON file:

```powershell
python scripts/evaluate.py --cases app/eval/cases.json --predictions eval_runs/predictions.json --save-report
```

The predictions JSON can be:

- a list of records with `case_id`;
- a mapping from case id to record;
- a previous eval report with `case_results`.

Supported prediction fields include:

- `answer` or `final_answer`
- `answer_mode`
- `document_candidates`, `selected_documents`, or `documents`
- `chunks` or `retrieved_chunks`
- `evidence_pack.items`, `evidence_items`, or `evidence`
- `evidence_pack.source_matches`, `final_sources`, or `sources`
- `discarded_candidates`
- `used_discarded_candidates`

## Metrics

- `document_precision`: expected document signals divided by selected document count.
- `source_precision`: shown sources matching expected document signals.
- `evidence_precision`: evidence/chunk coverage of expected documents, answer terms, and supported points.
- `answer_term_score`: coverage of expected answer terms, zeroed when forbidden answer terms appear.
- `forbidden_leakage`: `1.0` when forbidden document or answer terms appear in answer, evidence, or sources.
- `claim_grounding_score`: expected supported points present in answer and, for material answers, evidence.
- `final_score`: weighted aggregate with penalties for wrong mode, source overrun, missing required sources, sources in no-source modes such as `ask_for_missing_data`, `general_answer_without_sources`, or `out_of_base`, forbidden leakage, and discarded candidate use.

## Reports

With `--save-report`, the runner writes:

- `eval_runs/latest.json`
- `eval_runs/latest.md`
- `eval_runs/YYYYMMDD_HHMMSS.json`
- `eval_runs/YYYYMMDD_HHMMSS.md`

## Comparing Runs

```powershell
python scripts/compare_eval_runs.py eval_runs/baseline.json eval_runs/latest.json
```

The comparator flags regressions when:

- an expected document disappears;
- a forbidden document appears;
- source count exceeds the case limit;
- answer mode is wrong;
- sources appear for no-source modes such as `ask_for_missing_data`, `general_answer_without_sources`, or `out_of_base`;
- a discarded candidate is used;
- final score drops by `0.5` or more.

## CI Validation

The GitHub Actions workflow validates committed JSON files before tests run. Keep eval cases strict JSON, not JSONC: no comments, trailing commas, or unquoted strings.
