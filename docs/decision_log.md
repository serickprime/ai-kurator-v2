# Decision Log

## Evidence-first RAG remains the core architecture

Decision: keep RAG v2 evidence-first.

Reason: the bot must answer only from accepted evidence and show sources from the evidence actually used.

## External docs require curated candidates

Decision: external documentation sources must come from a curated candidates catalog or approved config.

Reason: arbitrary URLs can introduce stale, unofficial, noisy, or unsafe content.

## Arbitrary URL activation is forbidden

Decision: `/docs_preview` and `/docs_activate` reject arbitrary URLs.

Reason: user-provided URLs should not become trusted documentation sources without review.

## Preview before activation is required

Decision: every docs source must pass preview/dry-run before activation.

Reason: preview catches redirect problems, missing pages, bad domains, and weak candidates before indexing.

## Owner/admin confirmation is required for activation

Decision: activation requires explicit owner/admin confirmation.

Reason: activation writes to the knowledge base and affects answers.

## OpenRouter was the first controlled activation experiment

Decision: OpenRouter was used to test the full flow.

Result:

- Quality gate PASS.
- 25 pages fetched.
- 23 indexed new.
- 2 skipped unchanged.
- 0 failed.
- 264 chunks.
- RAG answered OpenRouter question using `openrouter_docs`.
- `/source_last` showed official `external_docs`.

## Docs UI Wizard should avoid per-service top-level buttons

Decision: do not add one top-level button per service in `/docs`.

Reason: as the number of services grows, the menu would become cluttered.

## Project progress must be tracked in repository docs

Decision: project status, roadmap, guardrails, and workflow must live in repository docs.

Reason: agents should not rely only on chat history.

## Query enrichment uses a curated retrieval glossary

Decision: use a curated query glossary to add technical search anchors for retrieval.

Reason: natural-language user questions can be semantically correct while missing exact method, parameter, node, or RPC names used in official docs. Query enrichment improves retrieval by adding exact terms, config terms, and query facets from a curated glossary.

Extensibility:

- `config/query_glossary.yaml` is a seed glossary, not a fixed list of all future topics;
- new services, course topics, uploaded-material themes, and official-doc anchors should be added through reviewed config changes over time;
- Python code must load glossary rules generically instead of hardcoding per-service rules;
- automatic discovery may suggest candidate rules, but owner/admin approval is required before applying them.

Boundaries:

- enrichment does not generate answers;
- enrichment does not replace evidence;
- enrichment does not change AnswerGenerator;
- the original user question is preserved;
- final sources still come only from accepted evidence.
- glossary candidates do not become trusted evidence.

Principle: do not fix one question with one-off code. Improve retrieval quality for a class of questions and keep regression tests for that class.

Status visibility: quality surfaces should show the reason for `WARN` or `FAIL`, not only the raw status label.

## Glossary candidate discovery is read-only and review-first

Decision: Phase 4A may suggest glossary candidates from existing read-only
runtime data, but it must not apply them automatically.

Reason: discovered terms are retrieval-anchor proposals, not evidence and not
answers. Owner/admin review is required before any candidate can update
`config/query_glossary.yaml` or affect query enrichment.

Boundaries:

- no Supabase writes;
- no changes to `config/query_glossary.yaml`;
- no crawl, sync, indexing, reindex, or activation;
- no AnswerGenerator, retrieval/router, or RAG pipeline changes;
- no one-question hardcoded fixes.

## Glossary candidate apply requires explicit review

Decision: Phase 4B may create owner/admin review files and reviewed glossary
outputs, but candidates must not be applied automatically.

Reason: glossary candidates are retrieval-anchor proposals. They can improve
future retrieval only after an owner/admin has approved, rejected, or edited
them.

Boundaries:

- pending and rejected candidates are skipped;
- edited candidates use `edited_terms`;
- duplicates from the existing seed glossary are skipped;
- sensitive-review candidates require a separate `allow_sensitive_apply: true`;
- default apply output goes to `reports/` or `tmp/`;
- direct writes to `config/query_glossary.yaml` require both `--write-config`
  and `--confirm-reviewed-apply`;
- no Supabase writes, migrations, activation, crawl, sync, indexing, reindex,
  Telegram UI, AnswerGenerator, retrieval/router, or RAG pipeline changes in
  the Phase 4B CLI MVP.

## Service-aware suggestions are read-only previews

Decision: Phase 5A may detect known service mentions and report whether docs
are active, inactive, or missing, but it must not perform activation or
indexing.

Reason: ordinary user questions should not trigger docs connection work. The
owner/admin needs a preview boundary before any future docs preview,
activation, crawl, sync, indexing, or config change.

Boundaries:

- suggestions are not answers and not evidence;
- active services continue through the normal RAG flow without owner
  suggestion;
- unknown or ambiguous services must not be treated as confident detections;
- detection aliases can be extended through config without Python hardcoding;
- Telegram owner UI is future Phase 5B, not part of the Phase 5A CLI MVP;
- no Supabase writes, migrations, activation, crawl, sync, indexing, reindex,
  AnswerGenerator, retrieval/router, RAG pipeline, or schema changes.

## Service suggestion Telegram preview is owner/admin-only

Decision: Phase 5B adds an explicit owner/admin Telegram command for the
Phase 5A service-aware suggestion preview instead of adding suggestions to
ordinary RAG answers.

Reason: normal user questions should stay on the existing RAG path. Technical
docs-availability previews are an owner/admin review tool and must not become
implicit activation or indexing requests.

Boundaries:

- ordinary users do not receive the technical preview;
- handlers stay thin and delegate detection/formatting to a feature module;
- the command is preview-only and does not call `/docs_preview` automatically;
- no docs registration, activation, crawl, sync, indexing, reindex, config
  writes, Supabase writes, migrations, AnswerGenerator, retrieval/router, or
  RAG pipeline changes are part of Phase 5B.

## Docs source health report is read-only

Decision: Phase 6A adds a service-layer and CLI report for docs source health
and staleness, but it must not repair or refresh sources automatically.

Reason: source health visibility should explain current WARN/FAIL/STALE states
without turning a status check into activation, crawl, sync, indexing, or
configuration work.

Boundaries:

- staleness is reported separately from operational quality failures;
- missing timestamps are `unknown/not available`, not automatic failures;
- runtime unavailable is reported as not verified, not as source failure;
- safe next actions are owner/admin recommendations only;
- no Telegram UI is part of Phase 6A;
- no Supabase writes, migrations, activation, crawl, sync, indexing, reindex,
  AnswerGenerator, retrieval/router, RAG pipeline, schema, or normal user flow
  changes are part of Phase 6A.

## Docs health Telegram preview is owner/admin-only

Decision: Phase 6B exposes the Phase 6A docs health report through an explicit
owner/admin Telegram command instead of adding health notices to ordinary RAG
answers.

Reason: docs source health is an operational owner/admin preview. It should
explain WARN, FAIL, inactive, stale, and runtime-unavailable states without
turning a status check into remediation work.

Boundaries:

- ordinary users do not receive the technical health report;
- handlers stay thin and delegate report formatting to a feature module;
- the command is preview-only and does not run refresh, repair, activation,
  crawl, sync, indexing, reindex, migrations, status edits, action callbacks,
  config writes, or Supabase writes;
- operational failure and staleness remain separate in the preview;
- OpenRouter WARN and Telegram Bot API FAIL remediation is a separate future
  owner-approved block.

## Source quality remediation starts offline

Decision: Phase 7A improves external docs cleaning and quality validation with
local fixtures and tests before any live source reprocessing.

Reason: OpenRouter WARN and Telegram Bot API FAIL are quality/cleaning issues,
not staleness or connectivity issues. Code and validation behavior should be
made safe first, then a separate owner-approved block can decide whether to
refresh or reindex affected sources.

Boundaries:

- existing Supabase documents/chunks are not modified in Phase 7A;
- no activation, crawl, sync, indexing, reindex, refresh, migrations, schema
  changes, or Supabase writes are part of Phase 7A;
- quality gate must still catch real page garbage and should not be weakened
  just to turn statuses green;
- useful endpoints, method names, parameters, code blocks, and safe inline HTML
  examples must be preserved;
- runtime health may continue to report existing OpenRouter WARN and Telegram
  Bot API FAIL until a future owner-approved reprocessing block.

## Source reprocessing requires preflight and baseline validation

Decision: Phase 7B.0 adds read-only source-scoped planning, baseline manifest
export, manifest verification, live drift detection, and reusable execution
precondition checks before any reprocessing is allowed.

Reason: the existing activation path performs per-page versioned replacement
and refreshes workspace-wide term statistics without one transaction for the
whole source. A future reprocessing task needs a verified baseline and exact
source scope before any owner-approved writes.

Boundaries:

- export and verification are preparation only, not approval to execute;
- scope must resolve to one service and one canonical source;
- manifest checksum, rollback capability, and live baseline drift must be
  checked before future execution;
- OpenRouter is the first controlled pilot; Telegram Bot API remains a
  separate later approval;
- no `/docs_activate`, activation, crawl, sync, indexing, reindex,
  reprocessing, rollback writes, migrations, schema changes, Supabase writes,
  AnswerGenerator, retrieval/router, query enrichment, or RAG pipeline changes
  are part of Phase 7B.0.

## Obsolete-page reconciliation is review-first

Decision: Phase 7B.1b adds generic read-only reconciliation planning before any
archive decision for active external-docs pages that are absent from a newly
discovered snapshot.

Reason: versioned activation can leave old active pages when a later crawl no
longer discovers them, and a missing page is not automatically obsolete. The
system needs a source-scoped plan that separates common pages, newly discovered
pages, missing active pages, possible superseded pages, ambiguous cases, and
canonical collisions before an owner reviews specific keys.

Boundaries:

- reconciliation uses registry/config scope, canonical URL normalization,
  generic document metadata, and declarative review decisions;
- OpenRouter is only the pilot fixture for the current incident;
- production logic must not branch on service ids such as `openrouter` or
  `telegram_bot_api`;
- no documents are archived, deleted, activated, crawled, indexed, reindexed,
  or written to Supabase in Phase 7B.1b;
- future archive execution requires merged tooling, a valid discovered
  snapshot, owner review of specific keys, and separate owner approval.

## Reviewed external-doc archive must be exact and backup-gated

Decision: Phase 7B.1g-A adds generic tooling to preview and, in a future
owner-approved block, archive exactly one reviewed external-doc document by
exact document id, key, source, workspace, status, version, and live inventory
fingerprint.

Reason: existing uploaded/local material archive commands intentionally reject
external/official docs, while the low-level `archive_active_documents` helper is
too broad for a reviewed production docs cleanup. External-doc archive needs a
reviewed reconciliation artifact, fresh rollback-capable backup, successor
validation when applicable, and optimistic drift checks before any write.

Boundaries:

- preview is the default and automatic archive remains disabled;
- production archive requires a separate owner-approved execution block and an
  exact confirmation phrase;
- future execution updates only one `documents` row from `active` to
  `archived` and does not delete cards, sections, chunks, embeddings, or the
  successor document;
- term-statistics refresh is a separate post-archive step and partial failure
  must be reported without automatic retry or rollback;
- OpenRouter is only a pilot fixture; production logic must remain
  registry/config-driven and source-agnostic;
- Plan B targeted reprocessing for keep-active documents is a separate future
  block.

## Reviewed keep-active external-doc reprocessing is exact-key only

Decision: Phase 7B.1g-B adds generic tooling to preview and, in a future
owner-approved block, reprocess only exact reviewed keep-active external-doc
document ids.

Reason: after the superseded OpenRouter MCP page was archived, two reviewed
keep-active pages still need cleanup without a full source crawl and without
touching unrelated documents. The operation needs exact target selection,
registered source scope, reviewed owner decisions, fresh post-archive rollback
backup, live drift checks, and all-target validation before any indexing writes.

Boundaries:

- preview is the default and performs no network fetch or Supabase writes;
- arbitrary URL input and full source crawl are disabled;
- future execution requires `keep_active` reviewed decisions, a fresh
  rollback-capable backup, URL allow-policy validation, exact confirmation, and
  all selected targets passing fetch/extract/clean/content-preservation gates
  before writes begin;
- future writes may only create new versions for the exact reviewed keys,
  archive their previous active versions, and refresh workspace term statistics
  once after full success;
- partial failure must be reported without automatic retry or rollback;
- OpenRouter remains only the pilot fixture; production logic must stay generic
  for registered external docs sources.

## Canonical relocation requires a dedicated reviewed path

Decision: Phase 7B.1g-C adds generic tooling for a reviewed external-doc
canonical relocation where the old and new canonical keys differ.

Reason: a confirmed canonical relocation is not a same-key version replacement.
The old key and new key are distinct document keys, and a keep-active decision
for the old key is not sufficient owner approval to create a new canonical key
and archive the old document.

Boundaries:

- relocation needs a dedicated owner-reviewed canonical relocation artifact;
- preview is the default and performs no fetch or writes;
- arbitrary URL input, batch mode, and full source crawl are disabled;
- future execution may fetch only the reviewed new canonical URL, create the
  new key-local document first, then archive the exact old document after the
  new active document is validated;
- new-key collisions, live drift, missing rollback-capable backup, and invalid
  useful-content preservation block execution readiness;
- partial failure is reported without automatic retry or rollback;
- OpenRouter remains only the pilot fixture; production logic must stay generic
  for registered external docs sources.

## Development workflow should stay streamlined

Decision: keep one active roadmap focus and avoid automatic GitHub/docs loops
after every completed block.

Reason: the project is owned by a solo developer. Branches, commits, checks,
feature-branch backups, and explicit owner-controlled local merges are enough
for normal safety. Extra GitHub/process loops are useful only when they
unblock the next agent, fix misleading guardrails/status, record an
architecture decision, or the owner explicitly asks.

Boundaries:

- GitHub is the durable remote Git store for commits, branches, tags, and
  `main`;
- a Pull Request is not required by default;
- PR is required only when the owner asks, for schema/migrations, high-risk
  production writes, large risky refactors, or multi-person collaboration;
- noticeable changes should use one focused feature branch, checks, a clear
  commit, feature-branch push as backup, explicit owner-approved local merge,
  necessary post-merge checks, and normal `main` push;
- small low-risk changes may be done directly on `main` after checking a clean
  state;
- never force-push;
- do not delete the backup feature branch until published `main` has been
  verified;
- do not use GitHub UI, GitHub MCP, Playwright, or `gh` only for ordinary
  personal-repository management;
- manual smoke is for runtime or user-visible changes, not every docs-only
  block;
- backlog ideas stay outside the active branch unless the owner explicitly
  allows a small directly related docs rule update.

## External documentation is replaceable evidence, not the product goal

Decision: treat official external documentation as a replaceable knowledge
source inside the bot, not as the product's primary objective.

Reason: AI Kurator V2 is a Telegram evidence-first RAG assistant. Perfect docs
health counters do not matter unless documentation quality affects retrieval,
answers, or citations.

Boundaries:

- zero health warnings are not the product goal;
- do not repair individual stored chunks only to make counters green;
- dirty fragments require action only when they harm retrieval, answers, or
  citations;
- when a documentation source is broadly broken or stale, first identify and
  fix a generic ingestion/extraction problem when one exists, then archive or
  remove the broken imported version through an owner-approved safe operation,
  then fetch and index a clean replacement;
- do not accumulate service-specific Python patches;
- do not manually edit production chunks;
- uploaded materials and official documentation remain conceptually separate.

## Functional answer quality comes before new cleanup work

Decision: after Telegram Batch 1 closure, the next active roadmap focus is
Phase 7C-A: a safe no-write end-to-end answer harness and baseline.

Reason: the product risk has moved from source-health counters to whether real
Telegram questions produce useful evidence-backed answers with clean sources
and insufficient-evidence behavior.

Boundaries:

- the harness must use the actual QuestionAnalyzer, document router, evidence
  retriever, reranker, evidence pack builder, AnswerGenerator, ClaimVerifier,
  and source formatter;
- EvidenceLogRepository writes must be disabled or replaced with a no-op;
- no real Telegram messages are sent;
- no production writes are performed;
- production reads require explicit approval;
- do not fix routing, aliases, evidence allocation, prompts, or citations
  before the baseline identifies the real blocker.

## Conversation history is context, not evidence

Decision: future follow-up support may use bounded conversation history as
dialog context only.

Reason: previous assistant answers can contain omissions or mistakes. Evidence
for every answer must still come from active uploaded materials and approved
official documentation.

Boundaries:

- do not treat previous assistant answers as trusted evidence;
- preserve evidence-first retrieval for every new question;
- prevent one user's history from leaking to another;
- use bounded history or summaries to control context size;
- keep Telegram handlers thin and put chat-management logic in feature/service
  modules.

## Phase 7B.2 closed Telegram Batch 1

Decision: Phase 7B.2 is complete and Telegram Batch 1 is closed.

Result:

- Telegram Bot API controlled reprocessing completed;
- active v2 target clean;
- archived v1 excluded from active retrieval;
- required terms present;
- OpenRouter remains healthy;
- remaining Webhooks screenshot/page residue and navigation/footer markers are
  deferred.

Boundary: deferred residue is not a blocker unless a future end-to-end answer
audit proves that it pollutes retrieval, displaces useful evidence, enters
final answer context, appears in final answers, or creates incorrect citations.

## Project handoff context is required before nontrivial work

Decision: agents must read `docs/project_handoff_context.md` before nontrivial
work.

Reason: project direction, GitHub repository, local path, Supabase lookup rules,
git/PR workflow, connected docs state, and forbidden actions must be available
from repository files without relying on chat history.

## Prompting playbook is required before prompt work

Decision: agents must read `docs/prompting_playbook.md` before writing or
changing prompts.

Reason: prompts should consistently include scope, guardrails, checks, git
workflow, final report requirements, and the retrieval/query quality principle.

## Secrets do not belong in the repository

Decision: real secrets, local credentials, `.env`, service role keys, GitHub
PATs, Telegram bot tokens, and logs with secrets must not be committed.

Reason: the bot uses privileged server-side keys and Telegram tokens. The repo
must contain only placeholders and safe examples.
