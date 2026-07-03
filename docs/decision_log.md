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
