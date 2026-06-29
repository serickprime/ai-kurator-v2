# RAG Development Policy

This project is an evidence-first Telegram RAG bot for a growing knowledge base. The goal is not to answer one known question correctly. The goal is to preserve a universal process that keeps retrieval broad, evidence strict, generation grounded, and sources precise.

## Evidence-First Contract

The required runtime contract is:

```text
question
-> question analysis
-> document router
-> evidence retrieval inside selected documents
-> evidence pack
-> answer generation only from evidence pack
-> claim verification
-> sources only from used evidence
```

The answer model may see only:

- `QuestionAnalysis`;
- `EvidencePack`;
- the original user question;
- compact `dialog_context`.

It must not see raw candidates, discarded candidates, document candidates, course hints, domain hints, or unrelated retrieval traces.

## Do Not Fix Single Questions

Do not add rules that recognize one product, one service, one error text, or one eval question and then force a result.

Bad examples:

- `if question contains "n8n", boost install lesson`;
- `if question contains "webhook", exclude payment lessons`;
- `if question contains "Supabase", use pgvector docs`;
- `if question contains "lemon", choose lemon_tree_care.md`;
- tuning a benchmark by memorizing its expected documents.

Good fixes improve a reusable layer:

- better question analysis;
- stronger document-card quality;
- clearer distinction between routing signal and accepted evidence;
- stricter evidence pack construction;
- claim verification that removes unsupported claims;
- eval coverage across different domains.

## Fixed Term Lists Are Not Enough

Small term lists can help normalize text, remove stopwords, or classify a broad task type. They must not become the main retrieval logic.

The knowledge base will contain lessons, homework, course rules, course catalogs, module structures, technical instructions, student questions, raw page exports, and allowed external documentation. A fixed service dictionary cannot scale across that corpus.

Prefer corpus-aware signals:

- document cards with summaries, topics, questions answered, entities, task types, and `not_about`;
- object/action/environment/symptom facets;
- rare anchor terms from the current workspace;
- answerability checks;
- evidence-level support checks.

## Routing Signal Vs Evidence

Routing signals are allowed to be broad. They help choose where to look.

Examples of routing signals:

- document title;
- course name;
- module or lesson name;
- document card topics;
- questions answered by the document card;
- lexical or vector similarity;
- domain hint;
- course hint.

Evidence is narrower. Evidence is text that can directly support a claim in the final answer.

Examples of accepted evidence:

- a chunk that states the command, setting, rule, deadline, or procedure being answered;
- a section that directly describes the relevant homework check rule;
- an official docs passage that directly supports the API behavior being described;
- a course-catalog row that directly states a course title or condition.

A course hint can route the search, but it cannot be cited as proof of an answer.

## Accepted Evidence

Accepted evidence must satisfy all of these:

- it is inside the selected document set or approved external-doc flow;
- it directly addresses at least one evidence question from `QuestionAnalysis`;
- it supports a concrete claim, not only a related topic;
- it is narrow enough to be shown in `EvidencePack`;
- it is marked as source only if it can be cited for the answer.

If evidence is partial, the answer mode should be `partial_answer`, not a confident full answer.

## Discarded Evidence

Discarded evidence includes:

- raw candidates that were retrieved but not accepted into `EvidencePack`;
- chunks with only common terms;
- chunks that mention the platform but answer a different task;
- document candidates with no supporting span;
- course or domain hints;
- partial evidence not marked as source;
- external docs that support a different subquestion.

Discarded evidence must not enter the generation prompt and must not be used to build sources.

## When To Use `out_of_base`

Use `out_of_base` when the user asks for an answer from the knowledge base, but no accepted evidence supports the answer.

The bot should say that the materials do not contain enough confirmed information. It should not invent course details, deadlines, commands, settings, API parameters, homework rules, or source names.

Sources must be empty in this mode.

## When To Use `ask_for_missing_data`

Use `ask_for_missing_data` when the question cannot be answered because the user omitted necessary input.

Examples:

- screenshot question without a screenshot or visible text;
- debugging request without the exact error text;
- homework review request without the homework or criteria;
- ambiguous follow-up with no recoverable dialog context.

The bot should ask for the specific missing data and avoid doing a fake diagnosis.

Sources must be empty in this mode.

## Sources Come Only From EvidencePack

Sources are built only from `EvidencePack.source_matches`.

Never build sources from:

- raw retrieval candidates;
- discarded candidates;
- document candidates;
- routed document titles;
- course hints;
- domain hints;
- partial evidence that is not marked as source.

This rule is what prevents the old bug where the answer used one correct chunk but showed unrelated lessons as sources because they shared common words.
