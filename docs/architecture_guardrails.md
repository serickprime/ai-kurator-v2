# Architecture Guardrails

These rules protect the project from uncontrolled growth and accidental regressions.

## Project context rules

- GitHub repository: `serickprime/ai-kurator-v2`.
- Local path: `D:\Downloads\ai-kurator-v2`.
- Before nontrivial work, read `docs/project_handoff_context.md`.
- Before writing or changing prompts, read `docs/prompting_playbook.md`.
- Do not rely on chat history as the only source of project state.

## Core RAG rules

- Do not change RAG pipeline unless explicitly requested.
- Do not change AnswerGenerator unless explicitly requested.
- Do not change retrieval/router unless explicitly requested.
- Answers must remain evidence-first.
- Sources must come from accepted evidence.
- Do not include raw candidate chunks directly in final answer generation.
- Do not fix one user question with one-off Python logic; improve a general retrieval/evidence mechanism.

## Query quality and glossary rules

- `config/query_glossary.yaml` is an extensible seed glossary, not a final topic catalog.
- Query glossary entries are retrieval anchors only; they are not answers and not evidence.
- New services, uploaded-material topics, course topics, and official-doc anchors should be added through config or reviewed glossary candidates.
- Do not hardcode per-service or per-question query enrichment rules in Python.
- Query enrichment must preserve the original user question.
- Query enrichment must not change AnswerGenerator, replace evidence, or bypass evidence-first source flow.
- Automatic glossary candidate discovery may suggest rules, but owner/admin approval is required before applying them.

## External docs rules

- Do not crawl arbitrary user URLs.
- Do not activate docs from arbitrary URLs.
- Use only curated candidates or approved official domains.
- Preview must happen before activation.
- Owner/admin confirmation is required for activation.
- Do not run `/docs_activate <service> confirm` unless explicitly requested.
- Do not run crawl, sync, indexing, or activation unless explicitly requested.
- External docs and uploaded materials must stay conceptually separate.
- External docs must not be archived through ordinary material commands.

## Telegram architecture rules

- Telegram handlers should stay thin.
- Business logic should live in feature/service modules.
- UI callbacks must not run activation confirm.
- UI callbacks must not crawl, sync, index, or write to Supabase.
- Buttons should be universal where possible; avoid one top-level button per service.

## Database and secrets rules

- Do not change Supabase schema without explicit approval.
- Start Supabase data/table lookup from `app/db/schema.sql`, `app/db/repositories.py`, and read-only scripts.
- Do not delete Supabase data manually without checking code paths and getting explicit approval.
- Do not touch `.env`.
- Do not reveal secrets.
- Do not print keys, tokens, or service role values.
- Secrets are not stored in this repository.
- Prefer fake services in tests.

## Workflow rules

- One branch = one meaningful block.
- Keep PRs small.
- Start work from fresh `main` unless the user gives a different branch.
- Push feature branches only after requested checks pass.
- Open PRs only when requested.
- Do not merge PRs unless explicitly requested.
- Merge only when CI is green and the PR is clean/mergeable.
- Prefer squash merge when the user asks to merge.
- Do not start the next roadmap item without explicit instruction.
- Every completed project block must update docs/project_status.md.
- Manual smoke checks should be recorded or summarized before moving to the next risky block.
