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

## Running

```powershell
python scripts/evaluate.py --cases app/eval/cases.json --save-report
```

Without a predictions file, the runner produces a `not_run` report. This is deliberate: the eval layer should not invent RAG outputs when no live pipeline result was supplied.

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
- `final_score`: weighted aggregate with penalties for wrong mode, source overrun, missing required sources, sources in `ask_for_missing_data`, forbidden leakage, and discarded candidate use.

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
- sources appear for `ask_for_missing_data`;
- a discarded candidate is used;
- final score drops by `0.5` or more.
