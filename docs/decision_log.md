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

## Development workflow should stay streamlined

Decision: keep one active roadmap focus and avoid automatic GitHub/docs loops
after every completed PR.

Reason: branch, tests, PR review, CI, and explicit owner-controlled merge are
enough for normal safety. Extra sanity loops and docs-only PRs are useful only
when they unblock the next agent, fix misleading guardrails/status, record an
architecture decision, or the owner explicitly asks.

Boundaries:

- PR merge still requires explicit owner command;
- CI must be green and the PR must be clean/mergeable before merge;
- manual smoke is for runtime or user-visible changes, not every docs-only PR;
- backlog ideas stay outside the active branch unless the owner explicitly
  allows a small directly related docs rule update.

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
