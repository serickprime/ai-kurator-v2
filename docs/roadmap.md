# Roadmap

## Roadmap focus discipline

Use one active roadmap focus at a time. By explicit owner request on
2026-07-19, the current focus is General Improvement Block 1: Documentation
Discovery must remain advisory and must not replace the normal evidence-first
answer to an ordinary text question.

The Phase 7C-B implementation changes and Documentation Discovery MVP code are
already present in `main` at `02b8693`. Phase 7C-B still needs a separately
approved post-change production baseline before it can be called closed.

During the current block, do not expand service detection/ranking, change RAG
retrieval or AnswerGenerator, edit prompts, run production audits, send real
Telegram messages, crawl, sync, index, reindex, activate docs, archive
documents, change schema, or write to Supabase.

Backlog items should be recorded without being started in the active branch:

- explicit owner-approved docs refresh flow;
- replace demonstrably broken/stale documentation sources when functional
  evidence justifies it;
- owner-approved docs refresh/disable flows;
- optional web interface;
- additional supported services;
- deployment/monitoring improvements;
- future MCP setup.

If a new idea appears during an active branch, keep it as backlog or a
recommended next prompt. Do not mix unrelated changes into the current branch.

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

Status: Phase 7B.0 is complete and merged in PR #35.

Phase 7B.1 OpenRouter controlled reprocessing:

- OpenRouter was reprocessed only after a source-scoped backup was created,
  verified, and explicitly owner-approved;
- activation reported quality PASS, failed pages 0, duplicate active keys 0,
  and no foreign source rows;
- useful endpoints and code were preserved;
- acceptance remains open because three untouched active v1 pages were absent
  from the latest fetched set and still carry old generator boilerplate;
- rollback is not recommended because active data is not damaged;
- do not automatically proceed to Telegram Bot API.

Phase 7B.1b generic safe obsolete-page reconciliation planning:

- compare current active document keys with a local discovered-key snapshot for
  one registered source;
- classify common, newly discovered, missing active, possible superseded,
  ambiguous, and canonical-collision cases;
- export owner-review plans for missing/superseded candidates without applying
  decisions;
- treat missing from snapshot as review input, not automatic obsolete;
- require separate owner approval for any future archive;
- keep OpenRouter as a pilot fixture only, with no service-specific production
  branching;
- do not crawl, activate, archive, delete, update, index, reindex, write
  Supabase, run migrations, or change the RAG pipeline.

Status: Phase 7B.1b is complete and merged in PR #36. Phase 7B.1c recovered a
validated OpenRouter discovered snapshot from activation logs, Phase 7B.1d
prepared owner recommendations, Phase 7B.1e recorded the reviewed decisions in
a local artifact outside Git, and Phase 7B.1f split follow-up remediation into
two independent plans.

Phase 7B.1g-A reviewed one-document external-doc archive tooling:

- add generic preview/default tooling for archiving exactly one reviewed
  external-doc document in a future owner-approved execution block;
- require exact service/source/workspace scope, exact document id/key, reviewed
  reconciliation artifact, fresh post-activation rollback-capable backup,
  live drift validation, and explicit owner confirmation;
- update exactly one `documents` row in future execution and never delete
  cards, sections, chunks, embeddings, or successor documents;
- keep archive and targeted reprocessing as separate owner approvals and
  separate execution reports;
- keep OpenRouter as a pilot fixture only, with no service-specific production
  branching;
- do not archive production documents, run crawl/fetch, activation, indexing,
  reindex, targeted reprocessing, rollback, migrations, schema changes,
  term-statistics refresh, or Supabase writes during the implementation block.

Status: Phase 7B.1g-A is complete and merged in PR #37. Phase 7B.1i-A then
archived exactly one superseded OpenRouter `mcp-server` document by explicit
owner approval; the new `guides/overview/mcp-server` successor stayed active,
cards/sections/chunks were preserved, and term statistics refreshed
successfully.

Phase 7B.1g-B reviewed key-scoped external-doc reprocessing tooling:

- add generic preview/default tooling for reprocessing exact reviewed
  keep-active external-doc document ids in a future owner-approved execution
  block;
- use registered service/source configuration, exact live document metadata,
  reviewed decisions, fresh post-archive rollback-capable backup, URL scope
  validation, live drift gates, and explicit owner confirmation;
- disable full source crawl and arbitrary URL input;
- use the shared external-doc fetch/extract/Phase 7A cleaner/indexing path for
  future execution;
- require all selected targets to fetch, clean, and validate before any future
  writes begin;
- keep OpenRouter as a pilot fixture only, with no service-specific production
  branching;
- do not create a production backup, run production preview/fetch/reprocessing,
  write Supabase, refresh production term statistics, run rollback, or start
  Telegram Bot API in the implementation block.

Status: Phase 7B.1g-B is complete and merged in PR #38, with a small repeated
`--document-id` CLI parsing fix merged in PR #39. Phase 7B.1i-B then stopped
safely before writes because Service Tiers resolved to a different canonical
key; Phase 7B.1j-B confirmed that case as a canonical relocation.

Phase 7B.1g-C reviewed canonical relocation tooling:

- add generic preview/default tooling for exactly one owner-reviewed
  external-doc canonical relocation where old and new canonical keys differ;
- require a dedicated canonical relocation review artifact, exact old document
  id/key/source/workspace/version/hash/signature, fresh rollback-capable backup,
  live drift validation, collision checks for the new key, and explicit owner
  confirmation before any future execution;
- keep relocation separate from same-key version replacement: future execution
  creates the new key-local document first, then archives the exact old
  document only after the new active document is validated;
- disable arbitrary URL input, batch mode, and full source crawl;
- keep OpenRouter as a pilot fixture only, with no service-specific production
  branching;
- do not create a production relocation artifact or backup, run production
  preview/fetch/relocation, write Supabase, refresh production term statistics,
  run rollback, start App Attribution reprocessing, or start Telegram Bot API in
  the implementation block.

Phase 7B.2 Telegram Bot API controlled reprocessing:

- Telegram Bot API controlled reprocessing completed;
- active v2 target is clean;
- archived v1 is excluded from active retrieval;
- required terms are present;
- OpenRouter remains healthy;
- Telegram Batch 1 is formally closed with classification
  `batch1_closed_target_clean_remaining_webhooks_residue`;
- two Webhooks screenshot/page-residue chunks and six navigation/footer markers
  remain deferred as non-blocking residue.

Status: complete.

## Phase 7C - Functional bot answer quality

Goal: verify and improve the real user experience from question to final
answer. AI Kurator V2 is a Telegram evidence-first RAG assistant, not a
documentation-maintenance bot.

The bot must:

- accept questions from a user;
- understand topic, course context, and explicitly mentioned service;
- retrieve relevant evidence from uploaded course materials;
- retrieve relevant approved official documentation when a service is mentioned
  or current technical details are needed;
- combine course evidence and documentation evidence when both are relevant;
- generate practical answers only from accepted evidence;
- show understandable source references;
- exclude archived document versions;
- state uncertainty or request clarification when evidence is insufficient;
- avoid exposing UUIDs, raw chunks, debug metadata, and internal implementation
  details to ordinary users.

### Phase 7C-A - Safe end-to-end answer harness and baseline

Status: complete.

Durable outcome:

- reusable no-write harness added in `app/rag/quality_harness.py`;
- CLI added in `scripts/run_answer_quality_baseline.py`;
- focused regression tests added in `tests/test_answer_quality_harness.py`;
- harness uses the actual QuestionAnalyzer, router, retriever, reranker, evidence pack,
  AnswerGenerator, ClaimVerifier, and source formatter;
- `EvidenceLogRepository` is not created and pipeline logger is `None`;
- Telegram sending is not used;
- Supabase is wrapped by a read-only adapter allowing only `select`,
  `match_document_cards`, `hybrid_match_chunks_in_documents`, and
  `match_chunks_in_documents`;
- confirmed baseline wrote a sanitized local artifact outside Git and reported
  Supabase write attempts 0, blocked write calls 0, and non-allowlisted RPC
  attempts 0.

Baseline result:

- overall classification: `functional_blocker_found`;
- primary blocker: `evidence_selection_gap`;
- `n8n_docs` and `openrouter_docs` warned because routing selected relevant
  service documents but accepted evidence did not contain the expected
  high-signal terms;
- `mixed_course_service_auto` was `BLOCKED` because no suitable uploaded
  material/service fixture was found, so mixed course/docs routing is not yet
  proven broken;
- follow-up limitation was handled as a Phase 8B concern and was not selected
  as the Phase 7C-B blocker;
- dirty Telegram documentation residue did not enter final answers or
  citations in this baseline.

Future baseline command pattern:

```powershell
.\.venv\Scripts\python.exe scripts\run_answer_quality_baseline.py --output <external-json-path> --resume --answer-mode cheap --confirm-read-only-production
```

The artifact must stay outside Git.

Required baseline cases:

1. Course-material-only question.
2. Explicit Telegram Bot API question.
3. Explicit n8n question.
4. Explicit OpenRouter question.
5. Explicit Supabase question.
6. Mixed course task plus named service documentation.
7. Ambiguous service question.
8. Unsupported/out-of-base question.
9. Archived version exclusion.
10. Citation/source-label quality.
11. No internal IDs or debug data in final answer.
12. Follow-up-style question, recorded as unsupported or partial until Phase
    8B is implemented.
13. Optional text-plus-image case when vision runtime is available.

For every case evaluate service/topic detection, course routing,
documentation routing, selected documents, accepted evidence, archived evidence
exclusion, answer completeness, unsupported claims, citations,
insufficient-evidence behavior, and whether dirty documentation fragments enter
the answer.

### Phase 7C-B - One focused functional fix

Status: implementation changes are present in `main`; post-change production
validation is pending, so the phase is not closed.

Primary blocker selected by Phase 7C-A:

- `evidence_selection_gap`.

Phase 7C-B must implement exactly one generic fix for that blocker. It must not
combine course alias wiring, explicit service routing, mixed-source allocation,
answer generation, citation formatting, documentation repair, Phase 8A upload
cleanup, or Phase 8B conversation memory unless new evidence proves the chosen
one-block scope is wrong before edits begin.

Requirements:

- one focused branch;
- generic behavior;
- no production IDs;
- no service-specific Python `if` branches;
- regression tests for a class of questions;
- preserve evidence-first behavior;
- rerun the Phase 7C-A matrix after the fix.

Mixed course-plus-documentation evidence is an acceptance requirement and audit
question, not a claimed current defect until the harness proves it.

### General Improvement Block 1 - Advisory Docs Discovery

Status: active focused branch.

Goal: preserve the ordinary answer contract while still creating safe pending
documentation suggestions for unknown services.

Requirements:

- run the normal RAG answer path exactly once for every ordinary text question;
- run discovery as an advisory follow-up and keep its user notice non-technical;
- discovery/search failure must not remove or replace the answer;
- preserve the existing feature flag and owner review/activation safeguards;
- do not change unknown-service detection/ranking, crawl, indexing, activation,
  schema, AnswerGenerator, or retrieval in this block.

## Phase 8A - Uploaded File Lifecycle and Storage Hygiene

Status: planned, not started.

Goal: prevent temporary Telegram uploads from growing indefinitely.

Plan:

- treat original Telegram-uploaded files as temporary;
- remove originals after successful ingestion;
- remove or quarantine them after failures according to a bounded retention
  policy;
- clean abandoned files after expiry;
- retain indexed text, document cards, sections, chunks, embeddings, and safe
  metadata;
- do not depend on the original file after successful ingestion unless an
  explicit retention policy requires it;
- avoid storing unnecessary absolute local paths in user-facing metadata;
- protect against deleting files outside the managed upload directory;
- cover success, failure, retry, and expired-file cases with tests.

## Phase 8B - Conversation Memory and Chat Management

Status: planned, not started.

Goal: support genuine follow-up questions and manageable conversation history.

Plan:

- persist user and assistant messages;
- load a bounded recent dialog context for follow-up questions;
- preserve evidence-first retrieval for every new question;
- do not treat previous assistant answers as trusted evidence;
- keep "Новая тема";
- list previous conversations;
- switch to a selected conversation;
- continue an existing conversation;
- create a new conversation;
- safely handle missing/deleted conversations;
- use summaries or bounded history to control context size;
- avoid leaking one user's history to another;
- evaluate persistent user settings wiring;
- keep Telegram handlers thin.

Follow-up history is context only. It must never become evidence or replace
retrieval from active uploaded materials and approved official documentation.

## Documentation source replacement policy

External documentation is a replaceable knowledge source. Zero health warnings
are not the product goal.

- Do not repair individual stored chunks only to make counters green.
- Dirty fragments require action only when they harm retrieval, answers, or
  citations.
- When a documentation source is broadly broken or stale, identify and fix a
  generic ingestion/extraction problem when one exists, archive or remove the
  broken imported version through an owner-approved safe operation, then fetch
  and index a clean replacement.
- Do not accumulate service-specific Python patches.
- Do not manually edit production chunks.
- Uploaded materials and official documentation remain conceptually separate.

## Later roadmap

- Replace demonstrably broken/stale documentation sources when functional
  evidence justifies it.
- Owner-approved docs refresh/disable flows.
- Optional web interface.
- Additional supported services.
- Deployment/monitoring improvements.

Do not schedule or start later roadmap items automatically.

## Not planned now

- automatic indexing of arbitrary URLs;
- mass activation of all candidates without review;
- changing RAG pipeline;
- changing AnswerGenerator;
- changing Supabase schema without explicit approval.
