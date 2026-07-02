# Agent Workflow

This document defines how Codex or any coding agent should work in this repository.

## Before work

1. Read `AGENTS.md`.
2. Read `docs/project_status.md`.
3. Read `docs/roadmap.md`.
4. Read `docs/architecture_guardrails.md`.
5. Read the task-specific docs.
6. Check current git state.
7. Confirm internally that the requested task does not violate guardrails.
8. Do only the requested block.

If the task conflicts with guardrails, stop and report the conflict.

## During work

- Keep the PR small.
- Avoid unrelated refactors.
- Do not start the next roadmap item.
- Keep Telegram handlers thin.
- Put business logic in feature/service modules.
- Use fake services in tests.
- Do not use real Supabase unless explicitly required.
- Do not run crawl, sync, indexing, or activation unless explicitly required.
- Do not touch `.env`.

## After work

Run:

```powershell
.\.venv\Scripts\python.exe -m compileall app scripts tests
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts\check_tracked_secrets.py
.\.venv\Scripts\python.exe scripts\runtime_healthcheck.py
```
