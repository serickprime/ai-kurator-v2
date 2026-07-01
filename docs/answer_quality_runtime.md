# Answer Quality Runtime

This project keeps the RAG v2 contract evidence-first: answer generation sees only
`QuestionAnalysis`, `EvidencePack`, the user question, and compact dialog context.
Raw candidates, discarded candidates, and document candidates stay out of the
generation prompt.

## Model Fallback

OpenRouter models must be concrete ids such as `openai/gpt-4.1-mini` or another
`provider/model` value. Do not use abstract aliases like `openrouter/free` as a
generation model id.

Text generation uses the configured mode:

- `free`: tries only `OPENROUTER_FREE_TEXT_MODELS`.
- `cheap`: tries `OPENROUTER_CHEAP_TEXT_MODELS`.
- `quality`: tries `OPENROUTER_QUALITY_TEXT_MODELS`.

`quality` falls back to `cheap` only when `ALLOW_QUALITY_TO_CHEAP_FALLBACK=true`.
`free` does not silently switch to paid models.

If every configured model fails, the answer generator uses a deterministic
curator-style fallback from accepted evidence. It does not dump raw chunks and
does not expose internal pipeline terms to the Telegram user.

## OpenRouter 400

A 400 response usually means the model id or request payload is rejected by
OpenRouter. The client records sanitized provider errors and the router tries the
next allowed concrete model in the same policy chain.

Check model configuration with:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\smoke_openrouter_models.py
```

The smoke script prints invalid abstract ids without printing the API key.

## Evidence Quality

Evidence reranking prefers chunks that can actually answer the question:

- coverage of `evidence_questions` and `must_answer_points`;
- concrete actions, commands, config keys, file names, examples, and checks;
- exact anchors from the user question;
- matching headings;
- lower score for short, navigational, or generic chunks.

Inspect the latest evidence log with:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\inspect_last_evidence_log.py
```

Useful debug fields:

- `llm_model_attempts`
- `llm_errors_sanitized`
- `final_model_used`
- `fallback_used`
- `evidence_decisions`
- `source_label_debug`
- `reranker_score_breakdown`

## Source Labels

Sources are built only from `EvidencePack.source_matches`. The label builder
cleans empty titles, `Название файла:`, `Прочее`, `unknown`, duplicate parts,
file extensions, and path prefixes.

Run a quick quality check on the latest answer:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe scripts\evaluate_answer_quality_smoke.py
```

This checks for raw evidence dumps, internal pipeline terms, dirty source labels,
excessive evidence count, empty fallback answers, and sources on no-evidence
modes.
