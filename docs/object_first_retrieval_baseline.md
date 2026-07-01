# Object-First Retrieval Baseline

Baseline was captured before retrieval/router changes.

Commands:

```powershell
python -m compileall app scripts tests
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts\evaluate_retrieval_simple_synthetic.py --reingest
```

Results:

| Metric | Baseline |
|---|---:|
| total_cases | 38 |
| document_top1_accuracy | 0.7895 |
| document_top3_accuracy | 0.9474 |
| chunk_fact_recall | 0.7237 |
| evidence_precision | 0.8509 |
| forbidden_document_leakage | 0.0 |
| answer_mode_accuracy | 0.8947 |
| average_evidence_pack_size | 1.68 |

Baseline checks:

- `compileall`: passed.
- `pytest`: 47 passed.
- Synthetic benchmark wrote `eval_runs/retrieval_simple_synthetic/latest.json` and `.md`.
