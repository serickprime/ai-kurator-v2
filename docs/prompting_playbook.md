# Prompting Playbook

Use this file before writing new prompts for agents or model behavior in this
repository. The goal is continuity: a future agent should understand the task,
guardrails, and verification path without reading chat history.

## Required Prompt Context

Every substantial prompt should include:

- repository: `serickprime/ai-kurator-v2`;
- local path: `D:\Downloads\ai-kurator-v2`;
- target branch or branch to create;
- current recorded baseline or milestone, and the exact current `main` commit
  only when the task depends on it;
- exact task objective;
- files or modules to inspect;
- explicit forbidden actions;
- expected tests/checks;
- commit, push, and PR instructions;
- final report fields.

Every prompt that asks an agent to report results should also require a
`Recommended next prompt` block at the end of the final answer. The block is a
recommendation only; it must not authorize the agent to start the next project
block without an explicit owner command.

Prompts should preserve one active roadmap focus. State the current focus and
what must not be started in the same branch. For the current Phase 4A branch,
do not start Phase 4B, Supabase setup docs, MCP, or unrelated work until Phase
4A is merged or the owner explicitly changes focus.

Do not treat `docs/project_status.md` as an automatic latest-main pointer.
Project status should record durable project state and meaningful milestones.
When an exact commit matters, ask the agent to check `git log --oneline -5` or
GitHub instead of manually maintaining a latest-commit field after every
docs-only merge.

## Streamlined Workflow Prompts

Use the shortest workflow that preserves safety:

1. implement one focused block;
2. run checks;
3. commit and push;
4. open a PR only when requested;
5. check CI and mergeability;
6. merge only after explicit owner command;
7. run manual smoke only for runtime or user-visible changes;
8. move to the next roadmap block only after explicit owner command.

Do not ask for a separate sanity check after every merge when the PR was
docs-only, CI was green, the tree is clean, project docs already use stable
baseline policy, and no conflict is visible.

Docs-only prompts should be used only when documentation blocks the next agent,
guardrails are outdated, roadmap/status docs are misleading, an architecture
decision must be recorded, or the owner explicitly asks. Do not create docs-only
work only to update latest commit values or for cosmetic cleanup.

Keep backlog separate from current focus. Backlog examples include Supabase
setup docs for a new developer, Phase 4B owner/admin review/apply flow,
docs health/stale refresh, long-running activation UX progress, and future MCP
setup.

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

## Recommended Next Prompt Block

Every final report from Codex or another agent must end with a block named
`Recommended next prompt`.

The block must include:

- what should be done next;
- why that is the logical next step;
- why the agent is not starting it automatically;
- which guardrails matter for that next step;
- a ready-to-copy prompt for the owner to use if they approve the next block.

Use this format:

````markdown
## Recommended next prompt

Why this is the next step:
<short explanation>

Why I am not starting it:
<short explanation, such as: owner review, manual smoke, merge PR, or explicit confirmation is required>

Copy-paste prompt:

```text
Ты работаешь в проекте:

D:\Downloads\ai-kurator-v2

GitHub repository:

serickprime/ai-kurator-v2

Контекст

...

Цель

...

Важно

...

Шаги

...

Проверки

...

Ответ

...
```
````

The copy-paste prompt must repeat relevant guardrails instead of relying on
chat history. It should be specific enough for the next agent to work safely,
but it must not ask the agent to begin any later roadmap item unless the owner
has explicitly chosen that next step.

The recommended prompt should be useful without creating extra process loops:

- do not recommend a sanity check by default after every merge;
- do not recommend docs-only cleanup unless there is a real blocking reason;
- if a feature branch is ready, recommend opening the PR;
- if a PR is open, recommend checking CI and merging after explicit owner
  approval;
- if a PR is merged and manual smoke is not needed, recommend the next roadmap
  block;
- if manual smoke is needed, recommend one short, concrete smoke check instead
  of a new docs loop.

## Final Report Template

Ask for a final answer that includes:

- current roadmap focus;
- branch;
- current PR, if any;
- commit;
- what changed;
- files changed;
- tests added;
- compileall status;
- pytest count;
- `scripts/check_tracked_secrets.py` status;
- `scripts/runtime_healthcheck.py` status;
- what was not touched;
- what to check manually;
- next roadmap step;
- what is explicitly not being started;
- whether working tree is clean;
- confirmation that `.env` and secrets were not touched;
- confirmation that activation/crawl/sync/indexing/reindex were not run;
- confirmation that Supabase schema/migrations were not run;
- `Recommended next prompt`;
- why that prompt is the next logical step;
- why the agent is not starting it automatically.
