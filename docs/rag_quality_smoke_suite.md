# RAG Quality Smoke Suite

Manual Telegram smoke suite for checking answer quality after runtime or knowledge-base changes. Run these checks in Telegram after the bot is started and the current Supabase base is available.

For each answer, verify that:

- the bot answers only from active evidence when sources are required;
- sources are shown only when the answer is evidence-backed;
- `/source_last` matches the final sources;
- `/debug_last` does not show irrelevant selected documents;
- external docs are marked as official/external;
- uploaded/local archived materials are not reused by RAG;
- formatting has no empty numbered items, orphaned "см. раздел" fragments, or broken source blocks.

## Uploaded/Local Materials

| Question | Expected Behavior | Expected Sources | Must Not Happen | Check After Answer |
| --- | --- | --- | --- | --- |
| `какая команда чтобы установить claude code?` | If the `ClaudeCode` material is archived, the bot says there is no confirmed fragment in active materials. If an active local material exists, answer from that material only. | Empty when archived; uploaded/local `ClaudeCode` only when active. | Do not answer from archived `ClaudeCode`. Do not use official docs as a substitute unless the user asks for official docs and indexed evidence exists. | `/source_last`, then `/debug_last` if the answer unexpectedly has sources. |
| `что такое CLAUDE.md?` | Answer from active uploaded/local material if present. If archived/missing, say there is no confirmed fragment. | Uploaded/local material with short id and `type: uploaded` or local source type. | Do not use n8n_docs or supabase_docs. Do not invent file behavior without evidence. | `/source_last` |
| `какие материалы сейчас загружены?` | This is a command-like/admin question. Prefer `/materials`; RAG should not invent a list from memory. | No RAG sources unless a material explicitly answers this. | Do not list fake files. | `/materials` |

## Official n8n Docs

| Question | Expected Behavior | Expected Sources | Must Not Happen | Check After Answer |
| --- | --- | --- | --- | --- |
| `как в n8n работает http request node?` | Evidence-backed explanation from official n8n docs. Text should be readable and connected. | `n8n_docs`, `type: external_docs`, `source: official`. URL should be present in final sources. | No empty `1.`, `2.` items. No orphaned `см. раздел`. Do not cite uploaded/local materials unless they are actually used evidence. | `/source_last` |
| `по официальной документации n8n что такое Nodes?` | Answer from indexed n8n docs if exact evidence exists. | `n8n_docs` official/external. | Do not answer from broad unrelated pages without object coverage. | `/source_last`, optionally `/debug_last` |
| `по официальной документации n8n что такое Build?` | Definition-style answer from indexed n8n docs. | `n8n_docs` official/external with URL. | Do not output only a source label. Do not omit source block when answer is evidence-backed. | `/source_last` |

## Official Supabase Docs

| Question | Expected Behavior | Expected Sources | Must Not Happen | Check After Answer |
| --- | --- | --- | --- | --- |
| `что такое row level security в Supabase?` | Answer from indexed Supabase docs if exact/relevant evidence exists. | `supabase_docs`, `type: external_docs`, `source: official`. | Do not use n8n docs. Do not invent policy details not supported by evidence. | `/source_last` |
| `по официальной документации Supabase что такое project?` | Answer from Supabase docs if evidence exists, otherwise say evidence is missing. | `supabase_docs` when answered. | Do not answer from broad unrelated sources. | `/source_last`, `/debug_last` on surprising answers. |
| `по официальной документации Supabase что такое match_documents?` | If the exact object is not indexed, answer out-of-base / no confirmed fragment. | Empty when missing. | Do not answer from a general Supabase page without `match_documents` evidence. | `/source_last`, `/debug_last` |

## Unknown / Not Enough Evidence

| Question | Expected Behavior | Expected Sources | Must Not Happen | Check After Answer |
| --- | --- | --- | --- | --- |
| `сколько расстояние вокруг земли?` | If no active material contains this, do not invent a knowledge-base answer. General answer without sources is acceptable only if classified as source-free general knowledge. | Empty unless active evidence exists. | Do not attach random n8n/Supabase sources. | `/source_last` should be empty or show no evidence-backed sources. |
| `какой дедлайн у курса?` | Ask for missing admin/source data or answer only from strict admin evidence. | Empty unless an active admin/course material contains the deadline. | Do not invent dates. | `/debug_last` |
| `что делать если сервер не открывается?` | Ask for missing context unless active materials contain a specific server/setup troubleshooting answer. | Only relevant setup/server docs if present. | Do not use workflow execution/log docs before UI opens. | `/source_last`, `/debug_last` |

## Source Management

| Action | Expected Behavior | Expected Sources | Must Not Happen | Check After Answer |
| --- | --- | --- | --- | --- |
| After any evidence-backed answer, run `/source_last`. | Shows short ids, title, type, and `uploaded` or `official` source origin. | Same documents as final answer sources. | Do not show raw JSON, full UUIDs, discarded candidates, or raw retrieval candidates. | `/source_last` |
| Run `/archive_source <short_id>` for an uploaded/local source from `/source_last`. | Archives the source through the materials archive flow. Re-asking should not use that document. | No new answer sources during archive command. | Do not physically delete chunks. Do not archive documents outside the last answer. | `/materials`, `/base_status`, then repeat the question. |
| Run `/archive_source n8n_docs` or another official source id. | Refuses with the official docs archive message. | No mutation. | Do not archive external docs through Telegram. | `/source_last` |
| Run `/archive_source missing`. | Says that the source is not in the last answer. | No mutation. | Do not search/archive arbitrary documents. | `/source_last` |

## Formatting Quality

| Question | Expected Behavior | Expected Sources | Must Not Happen | Check After Answer |
| --- | --- | --- | --- | --- |
| `как в n8n работает http request node?` | Connected explanation with source block at the end. | `n8n_docs` official/external. | No empty numbered lines like `1.` or `2.`. No orphaned `(см.` or `см. раздел`. No source label only answer. | `/source_last` |
| Ask a question that returns a command or code snippet from active material. | Code/commands stay copyable. | Relevant uploaded/local or official source. | Do not remove command lines, URLs, SQL, JSON, env/config examples, or fenced code blocks. | `/source_last` |
| Ask a broad explanation question from official docs. | Answer is concise but not over-compressed; source block remains. | Official docs source when evidence-backed. | Do not collapse useful paragraphs into one vague sentence. Do not remove links in source labels. | `/source_last` |
