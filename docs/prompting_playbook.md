# Prompting Playbook

Use this file before writing new prompts for agents or model behavior in this
repository. The goal is continuity: a future agent should understand the task,
guardrails, and verification path without reading chat history.

## Required Prompt Context

Every substantial prompt should include:

- repository: `serickprime/ai-kurator-v2`;
- local path: `D:\Downloads\ai-kurator-v2`;
- target branch or branch to create;
- current known main commit when relevant;
- exact task objective;
- files or modules to inspect;
- explicit forbidden actions;
- expected tests/checks;
- commit, push, and PR instructions;
- final report fields.

## Required Guardrails

Include the relevant guardrails directly in the task prompt:

- do not touch `.env`;
- do not reveal secrets;
- do not change Supabase schema or run migrations unless explicitly requested;
- do not run crawl, sync, indexing, activation, or reindex unless explicitly
  requested;
- do not run `/docs_activate <service> confirm` or `/docs_activate_ready
  confirm` unless explicitly requested;
- do not change RAG pipeline, AnswerGenerator, retrieval/router, or scoring
  unless the task is specifically about that layer;
- keep Telegram handlers thin and business logic in feature/service modules;
- do not fix one question point-wise; build or adjust a general
  retrieval/query quality layer.

## Retrieval And Query Quality Prompts

When asking for retrieval/query quality work, state that:

- `config/query_glossary.yaml` is a curated seed glossary, not a final catalog;
- glossary entries are retrieval anchors only, not answers;
- new topics should be added by config or reviewed glossary candidates, not
  Python hardcoding;
- glossary candidate discovery may suggest rules but owner/admin approval is
  required before applying them;
- the original user question must be preserved;
- accepted evidence remains required for final answers and sources.

## Docs Registry Prompts

When asking for docs registry work, state whether the work is:

- read-only dashboard/preview;
- activation plan only;
- activation confirm.

If it is not activation confirm, explicitly say:

- do not crawl;
- do not sync;
- do not index;
- do not write to Supabase;
- do not change config;
- do not activate candidates.

## Verification Block Template

Use this standard verification block unless the task is explicitly docs-only
and the user says otherwise:

```powershell
.\.venv\Scripts\python.exe -m compileall app scripts tests
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts\check_tracked_secrets.py
.\.venv\Scripts\python.exe scripts\runtime_healthcheck.py
```

Before commit, also ask the agent to check:

```powershell
git status -sb
git diff --stat
git diff -- . ":!*.env"
```

`runtime_healthcheck.py` can report `WARN` with exit code 0 when the warning is
limited to current service/docs quality status.

## PR Body Template

```text
What changed:

- ...

Checks:

- compileall: passed
- pytest: <count> passed
- scripts/check_tracked_secrets.py: passed
- scripts/runtime_healthcheck.py: OK/WARN with exit code 0

Important:

- RAG pipeline was not changed.
- AnswerGenerator was not changed.
- Supabase schema was not changed.
- External docs crawl/sync/indexing/activation was not run.
- Secrets were not added to the repository.
```

## Final Report Template

Ask for a final answer that includes:

- branch used;
- files changed;
- checks passed;
- pytest count;
- runtime_healthcheck status;
- whether working tree is clean;
- confirmation that `.env` and secrets were not touched;
- confirmation that activation/crawl/sync/indexing/reindex were not run;
- confirmation that Supabase schema/migrations were not run;
- next manual smoke step, if useful.

