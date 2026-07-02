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

## Telegram Bot API send-message queries use service-aware anchors

Decision: enrich Telegram Bot API send-message questions with `sendMessage`, `chat_id`, and `text` retrieval anchors.

Reason: Russian user phrasing such as "как отправить сообщение через Telegram Bot API?" can be semantically correct while missing the exact method name used in official docs. The enrichment keeps the original question unchanged, does not reindex documents, and only improves retrieval signals for the service-specific send-message intent.

Status visibility: quality surfaces should show the reason for `WARN` or `FAIL`, not only the raw status label.
