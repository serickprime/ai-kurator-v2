# Agent Instructions

This repository uses project control docs.

Before any code or docs changes, every agent must read:

- docs/project_status.md
- docs/roadmap.md
- docs/architecture_guardrails.md
- docs/agent_workflow.md
- docs/project_handoff_context.md for nontrivial work
- docs/prompting_playbook.md before writing or changing prompts

If the requested task conflicts with these files, stop and report the conflict.

Project identity:

- GitHub repository: `serickprime/ai-kurator-v2`
- Local path: `D:\Downloads\ai-kurator-v2`

Hard rules:

- Never touch `.env`.
- Never reveal secrets.
- Never run `/docs_activate <service> confirm` unless explicitly requested.
- Never run crawl, sync, indexing, or activation unless explicitly requested.
- Never change RAG pipeline unless explicitly requested.
- Never change AnswerGenerator unless explicitly requested.
- Never change retrieval/router unless explicitly requested.
- Never change Supabase schema without explicit approval.
- Do not accept arbitrary URLs for docs activation.
- Secrets are not stored in this repository; never commit `.env`, service role keys, GitHub PATs, Telegram bot tokens, or logs with secrets.
- Keep Telegram handlers thin.
- Business logic must live in feature/service modules.
- External docs and uploaded materials must stay conceptually separate.
- Do not fix one question point-wise; build or adjust a general retrieval/query quality layer.
- One branch = one meaningful block.
- Do not start the next roadmap item without explicit instruction.
- After a completed project block, update docs/project_status.md.
- Current active focus: Phase 7C-B - one focused fix for `evidence_selection_gap`, based on the completed Phase 7C-A baseline.
- Do not start Phase 7C-B without explicit instruction. The next fix must be generic, evidence-first, and limited to one primary blocker.

Before starting work:

1. Read AGENTS.md.
2. Read docs/project_status.md.
3. Read docs/roadmap.md.
4. Read docs/architecture_guardrails.md.
5. Read docs/agent_workflow.md.
6. Read docs/project_handoff_context.md for nontrivial work.
7. Read docs/prompting_playbook.md before prompt work.
8. Confirm the current task scope internally.
9. Do only the requested task.

Supabase lookup rules:

- Start from `app/db/schema.sql`, `app/db/repositories.py`, and read-only scripts before touching data.
- Do not run schema changes, migrations, manual deletes, activation, crawl, sync, indexing, or reindex unless explicitly requested.
- Keep active RAG evidence limited to active documents/chunks.

Git workflow rules:

- Solo-owner mode is the default. GitHub is the durable remote Git store for commits, branches, tags, and `main`; a Pull Request is not required by default.
- For noticeable changes: start from fresh `main`, create one focused feature branch, run required checks, commit intentional files, push the feature branch as a backup, locally merge to `main` only after explicit owner approval, rerun needed checks, then push `main` normally.
- For small low-risk changes, direct work on `main` is allowed after confirming a clean state.
- Open a PR only when the owner asks, for schema/migrations, high-risk production writes, large risky refactors, or multi-person collaboration.
- Never force-push.
- Do not delete a backup feature branch until published `main` has been verified.
- Do not use GitHub UI, GitHub MCP, Playwright, or `gh` only for ordinary personal-repository management.
- Do not create extra GitHub/process loops after every small block.
- Prefer squash merge when the owner asks to merge a PR.
