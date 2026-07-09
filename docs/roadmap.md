# Roadmap

## Roadmap focus discipline

Use one active roadmap focus at a time. The current focus is Phase 7B.0 - safe
source-scoped reprocessing preparation tooling. Until Phase 7B.0 is merged, do
not start real source reprocessing, docs refresh/indexing, activation,
Supabase setup docs, MCP, or other unrelated tasks unless the owner explicitly
changes focus.

Backlog items should be recorded without being started in the active branch:

- Supabase setup docs for a new developer;
- Phase 7B owner-approved reprocessing of affected docs sources;
- explicit owner-approved docs refresh flow;
- long-running activation UX progress;
- future MCP setup.

If a new idea appears during an active branch, keep it as backlog or a
recommended next prompt. Do not mix unrelated changes into the current PR.

## Phase 1 — Stabilize current Docs Registry

Goal: keep the current system understandable and safe before adding more automation.

Tasks:

- Add project control pack.
- Add `docs/project_handoff_context.md` as the first-stop context for future agents.
- Add `docs/prompting_playbook.md` for future task/prompt writing.
- Keep project status in repository docs.
- Keep manual smoke checklist current.
- Avoid new activation until control docs are merged.

## Phase 2 — Docs Activation Queue

Goal: stop adding services one by one through new custom code.

Status: merged in PR #19.

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

## Phase 3 — Retrieval Query Quality Framework

Goal: improve answers across uploaded materials and official docs by bridging natural-language user questions and technical documentation terms while keeping evidence-first architecture.

Planned behavior:

- use a curated query glossary for retrieval-only anchors;
- treat `config/query_glossary.yaml` as a seed glossary, not the final topic catalog;
- add exact terms, config terms, and query facets before document/chunk retrieval;
- allow new services/topics to be added by YAML config without Python code changes;
- preserve the original user question;
- require accepted evidence for final answers;
- keep sources from accepted evidence only.

Not allowed:

- one-off fixes per question;
- hardcoded service/topic rules in Python;
- generated answers from glossary entries;
- replacing evidence with glossary content;
- changing AnswerGenerator to guess without evidence.

Status: merged in PR #20.

## Phase 4 — Glossary Candidate Discovery

Goal: make query quality scalable as uploaded materials, courses, service docs, and official external docs keep growing.

Phase 4A read-only MVP:

- discover candidate retrieval anchors from the existing seed glossary, term statistics, evidence logs, active documents, document cards, sections, and chunks;
- group candidates by service/source/topic;
- print an owner-facing report only;
- do not write to Supabase;
- do not modify `config/query_glossary.yaml`;
- leave owner/admin review and apply flow to a later block.

Status: merged in PR #25, with candidate quality cleanup merged in PR #26.

Phase 4B CLI owner/admin review/apply MVP:

- export Phase 4A candidates to an owner-editable review file;
- require manual pending, approved, rejected, or edited decisions;
- validate review files before any apply plan;
- build a dry-run apply plan that skips pending, rejected, duplicates, and
  unconfirmed sensitive-review candidates;
- write a reviewed glossary copy under `reports/` or `tmp/` by default;
- allow direct `config/query_glossary.yaml` writes only with explicit
  `--write-config --confirm-reviewed-apply`;
- keep Telegram UI out of this block.

Status: merged in PR #27, with minimal-diff output cleanup merged in PR #28
and reviewed glossary additions batch 1 merged in PR #29.

Planned behavior:

- analyze newly indexed uploaded materials and external docs;
- detect frequent technical terms, methods, parameters, node names, endpoints, config keys, and RPC names;
- group candidate anchors by service/source/topic;
- suggest possible glossary rules to the owner/admin;
- require owner/admin review before applying any suggested rule.

Example future flow:

```text
new docs indexed
→ extract candidate terms
→ group by service/source
→ suggest glossary update
→ owner approves
→ query enrichment improves future retrieval
```

Not allowed:

- automatic trust for discovered terms;
- automatic application of unknown rules;
- answer generation from glossary candidates;
- crawl, sync, indexing, or activation from glossary discovery by itself.

This phase should extend the retrieval/query quality layer for all future
uploaded materials and official docs. It must not become a sequence of
hardcoded one-question fixes.

Status: Phase 4B CLI review/apply MVP is complete.

## Phase 5 — Service-aware suggestions

Goal: make the bot notice when a user asks about a service whose docs are not connected.

Phase 5A read-only MVP:

- user asks about a service;
- a service-layer detector uses existing registry aliases, docs candidates,
  query glossary aliases, and a small detection-only config seed;
- the CLI prints an owner/admin preview for known services whose docs are
  missing or inactive;
- active supported services return `supported-active` and continue through the
  normal RAG flow;
- unknown or ambiguous services do not create confident suggestions;
- suggestions never activate, crawl, sync, index, reindex, write to Supabase,
  or change `config/query_glossary.yaml`.

Status: Phase 5A is complete.

Phase 5B owner/admin preview integration:

- explicit owner/admin-only Telegram command for the same read-only preview
  boundary;
- preview shows detected service, confidence, active context services, docs
  registration/active status, owner-review need, safe next action, and auto
  activation disabled;
- keep ordinary user questions on the normal RAG path;
- keep handlers thin and do not run activation from callbacks;
- no Supabase writes, crawl, sync, indexing, reindex, migrations, config writes,
  RAG pipeline changes, retrieval/router changes, or AnswerGenerator changes.

Status: Phase 5B is complete and merged in PR #31.

## Phase 6 — Maintenance

Goal: keep connected official docs useful over time.

Phase 6A read-only Docs Source Health/Stale Report:

- report registered docs sources, service mapping, active state, last-known
  docs/quality status, status reasons, timestamps when available, stale state,
  document/chunk counts, owner-review need, and safe next action;
- separate staleness from operational WARN/FAIL;
- handle runtime unavailable as `unknown/not verified` instead of a source
  failure;
- expose a CLI report only;
- do not refresh, activate, crawl, sync, index, reindex, write Supabase, change
  schema, or change normal RAG flow.

Status: Phase 6A is complete and merged in PR #32.

Phase 6B owner/admin Telegram preview:

- explicit owner/admin-only `/docs_health` command for the same read-only docs
  health report;
- optional service filter, for example `/docs_health openrouter`;
- compact Telegram-friendly summary and source rows without wide tables;
- no technical report for unauthorized users;
- no automatic refresh, repair, activation, crawl, sync, indexing, reindex,
  status edit, Supabase write, migration, action callback, or normal RAG flow
  change.

Status: Phase 6B is complete and merged in PR #33.

## Phase 7 - Source quality remediation

Goal: improve the quality of already connected official docs without turning
health visibility into automatic refresh or indexing.

Phase 7A offline cleaning/quality MVP:

- improve external docs extraction/cleaning and quality validation using small
  synthetic fixtures;
- target the current OpenRouter generator-boilerplate WARN class and Telegram
  Bot API raw HTML/navigation/footer/cookie FAIL class;
- preserve useful endpoints, method names, parameter names, code blocks, model
  names, headers, and safe inline HTML examples;
- keep quality gate strict for real page garbage and readable about reasons;
- do not crawl, refresh, sync, index, reindex, activate, write Supabase, run
  migrations, change schema, or change normal RAG flow.

Status: Phase 7A is complete and merged in PR #34.

Phase 7B.0 safe reprocessing preparation tooling:

- build a read-only source-scoped preflight plan for one service/source;
- export a local baseline manifest with source-scoped rows, fingerprints, and
  checksum;
- verify manifest integrity and rollback capability offline;
- compare a manifest with live read-only inventory to detect baseline drift;
- expose reusable execution precondition validation for future Phase 7B.1;
- show expected write scope and known partial-failure risks;
- do not run `/docs_activate`, activation, crawl, sync, indexing, reindex,
  rollback writes, migrations, Supabase writes, or source reprocessing.

Phase 7B.1 future OpenRouter reprocessing:

- run only after Phase 7B.0 is merged, post-merge smoke passes, a real
  source-scoped backup is created and verified, and the owner explicitly
  approves exact OpenRouter execution commands;
- process OpenRouter as the first controlled pilot;
- verify docs health and retrieval/evidence quality after reprocessing;
- do not automatically proceed to Telegram Bot API.

Phase 7B.2 future Telegram Bot API reprocessing:

- run only after successful Phase 7B.1 and separate owner approval;
- process Telegram Bot API as a separate higher-volume operation;
- verify docs health and retrieval/evidence quality after reprocessing.

Later maintenance features:

- refresh connected docs;
- disable docs source;
- explicit owner-approved stale docs refresh flow;
- last refresh status in `/docs`.

## Not planned now

- automatic indexing of arbitrary URLs;
- mass activation of all candidates without review;
- changing RAG pipeline;
- changing AnswerGenerator;
- changing Supabase schema without explicit approval.
