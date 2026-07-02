# Roadmap

## Phase 1 — Stabilize current Docs Registry

Goal: keep the current system understandable and safe before adding more automation.

Tasks:

- Add project control pack.
- Keep project status in repository docs.
- Keep manual smoke checklist current.
- Avoid new activation until control docs are merged.

## Phase 2 — Docs Activation Queue

Goal: stop adding services one by one through new custom code.

Planned features:

- `/docs_preview_all` — preview all candidates from the curated catalog.
- `/docs_ready` — show candidates ready for activation.
- `/docs_activate_ready` — show an activation plan for all ready candidates.
- `/docs_activate_ready confirm` — activate only ready allowlisted candidates after owner/admin confirmation.

Classification:

- `ready` — low risk, pages found, no critical warning.
- `needs_review` — review risk or partial preview.
- `failed` — no pages found or loading errors.
- `already_connected` — docs source is already active.

Safeguards:

- no arbitrary URLs;
- no activation from ordinary user questions;
- no failed or needs_review candidates in batch activation;
- owner/admin confirmation required.

## Phase 3 — Service-aware suggestions

Goal: make the bot notice when a user asks about a service whose docs are not connected.

Planned behavior:

- user asks about a service;
- bot detects service by aliases;
- bot checks whether docs are connected;
- if docs are missing and candidate exists, bot suggests preview;
- bot does not auto-index from the normal question.

## Phase 4 — Maintenance

Goal: keep connected official docs useful over time.

Planned features:

- refresh connected docs;
- disable docs source;
- docs source health report;
- stale docs detection;
- last refresh status in `/docs`.

## Not planned now

- automatic indexing of arbitrary URLs;
- mass activation of all candidates without review;
- changing RAG pipeline;
- changing AnswerGenerator;
- changing Supabase schema without explicit approval.
