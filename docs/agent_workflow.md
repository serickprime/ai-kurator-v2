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

Use `git log --oneline -5`, `git status -sb`, or GitHub for the exact latest
`main` commit when it matters. `docs/project_status.md` records project state
and stable milestones; it is not an automatic latest-main pointer that must be
updated after every docs-only or status-only merge.

Repository identity:

- GitHub repository: `serickprime/ai-kurator-v2`
- Local path: `D:\Downloads\ai-kurator-v2`

## During work

- Keep the PR small.
- Avoid unrelated refactors.
- Do not start the next roadmap item.
- Keep one active roadmap focus at a time. The current focus remains active
  until its PR is merged or the owner explicitly changes direction.
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

## Streamlined development workflow

Use a short GitHub loop for normal feature blocks:

1. Implement one focused block.
2. Run the required checks.
3. Commit and push the feature branch.
4. Open a PR only when requested.
5. Check CI and mergeability.
6. Squash merge only after an explicit owner command.
7. Run manual smoke only when the change affects runtime or user-visible behavior.
8. Move to the next roadmap block only after the owner explicitly asks.

Do not create a separate post-merge sanity loop by default when the PR was
docs-only, CI was green, the working tree is clean, project docs already use
the stable baseline policy, and there is no sign of conflict.

Docs-only PRs should happen only when documentation blocks the next agent,
guardrails are outdated, roadmap/status docs are misleading, an architecture
decision must be recorded, or the owner explicitly asks. Do not make docs-only
PRs just to update a latest commit pointer or for cosmetic churn.

Backlog items must stay separate from the current focus. Record new ideas as a
recommended next prompt or backlog note, but do not mix unrelated changes into
the active PR. Small docs rule updates are allowed inside the active branch only
when the owner explicitly permits them and they directly protect the current
workflow.

Current Phase 5A focus:

- Phase 5A - Service-aware Suggestions read-only MVP.
- Do not start Phase 5B Telegram UI, Supabase setup docs, MCP, docs health
  refresh, or other unrelated tasks until Phase 5A is merged or the owner
  explicitly changes focus.

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

Also include:

- current roadmap focus;
- current branch or PR;
- next roadmap step;
- what is explicitly not being started.

End every report with a `Recommended next prompt` block. This block must:

- describe what should be done next;
- explain why it is the logical next step;
- explain why the agent is not starting it automatically;
- list the important guardrails for that next step;
- provide a ready-to-copy prompt.

The recommended prompt is only a recommendation. Do not start the next project
block, roadmap item, PR, merge, activation, crawl, sync, indexing, reindex, or
migration unless the owner explicitly requests that action.

The recommended prompt should not create extra process loops. Do not recommend
a default sanity check after every merge. If a feature branch is ready, the next
prompt may be to open the PR. If a PR is open, it may be to check CI and merge.
If a PR is merged and manual smoke is not needed, point to the next roadmap
block. If manual smoke is needed, recommend one short, concrete smoke check
instead of a new docs loop.
