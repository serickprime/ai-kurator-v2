# Agent Instructions

This repository uses project control docs.

Before any code or docs changes, every agent must read:

- docs/project_status.md
- docs/roadmap.md
- docs/architecture_guardrails.md
- docs/agent_workflow.md

If the requested task conflicts with these files, stop and report the conflict.

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
- Keep Telegram handlers thin.
- Business logic must live in feature/service modules.
- External docs and uploaded materials must stay conceptually separate.
- One branch = one meaningful block.
- Do not start the next roadmap item without explicit instruction.
- After a completed project block, update docs/project_status.md.

Before starting work:

1. Read AGENTS.md.
2. Read docs/project_status.md.
3. Read docs/roadmap.md.
4. Read docs/architecture_guardrails.md.
5. Read docs/agent_workflow.md.
6. Confirm the current task scope internally.
7. Do only the requested task.
