# Agent Workflow

This document defines how Codex or any coding agent should work in this repository.

## Before work

1. Read `AGENTS.md`.
2. Read `docs/project_status.md`.
3. Read `docs/roadmap.md`.
4. Read `docs/architecture_guardrails.md`.
5. Read `docs/project_handoff_context.md` for nontrivial work.
6. Read `docs/prompting_playbook.md` before writing or changing prompts.
7. Read the task-specific docs.
8. Check current git state.
9. Confirm internally that the requested task does not violate guardrails.
10. Do only the requested block.

If the task conflicts with guardrails, stop and report the conflict.

Repository identity:

- GitHub repository: `serickprime/ai-kurator-v2`
- Local path: `D:\Downloads\ai-kurator-v2`

## During work

- Keep the PR small.
- Avoid unrelated refactors.
- Do not start the next roadmap item.
- Keep Telegram handlers thin.
- Put business logic in feature/service modules.
- For retrieval query quality, prefer reviewed glossary/config updates over hardcoded Python rules.
- Treat `config/query_glossary.yaml` as a seed glossary; do not assume it covers every future topic.
- Do not apply automatically discovered glossary candidates without owner/admin approval.
- Use fake services in tests.
- Do not use real Supabase unless explicitly required.
- Do not run crawl, sync, indexing, or activation unless explicitly required.
- Do not touch `.env`.
- Do not store secrets in the repository.
- Do not fix one question point-wise; improve a general retrieval/query quality layer.

Supabase lookup:

- Start with `app/db/schema.sql`, `app/db/repositories.py`, and read-only scripts.
- Use repository/provider methods before raw table access in app code.
- Do not run schema changes, migrations, manual deletes, activation, crawl, sync, indexing, or reindex unless explicitly requested.

Git workflow:

- Start from fresh `main` unless the user gives a different branch.
- Use one branch per meaningful block.
- Commit only intentional files.
- Push only after checks pass.
- Open PRs only when requested.
- Do not merge PRs unless explicitly requested.
- Merge only when CI is green and the PR is clean/mergeable.
- Prefer squash merge when the user asks to merge.

## After work

Run:

```powershell
.\.venv\Scripts\python.exe -m compileall app scripts tests
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts\check_tracked_secrets.py
.\.venv\Scripts\python.exe scripts\runtime_healthcheck.py
```

Before commit, inspect:

```powershell
git status -sb
git diff --stat
git diff -- . ":!*.env"
```

Before push/PR, confirm no `.env`, local credentials, service role keys, GitHub
PATs, Telegram bot tokens, or secret-bearing logs are staged or untracked for
commit.

## Final report

Every final report must include the fields defined in
`docs/prompting_playbook.md`, including branch, commit, changed files, checks,
manual smoke notes, and confirmations about forbidden actions.

End every report with a `Recommended next prompt` block. This block must:

- describe what should be done next;
- explain why it is the logical next step;
- explain why the agent is not starting it automatically;
- list the important guardrails for that next step;
- provide a ready-to-copy prompt.

The recommended prompt is only a recommendation. Do not start the next project
block, roadmap item, PR, merge, activation, crawl, sync, indexing, reindex, or
migration unless the owner explicitly requests that action.
