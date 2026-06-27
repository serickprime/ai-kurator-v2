# Evaluation

The evaluation suite should focus on evidence quality, source precision, and refusal behavior.

## Core Metrics

- Document routing precision: selected documents include the expected lesson or source.
- Evidence precision: retrieved evidence directly supports the expected answer.
- Source precision: displayed sources come only from evidence used in the answer.
- Unsupported answer rate: the bot does not invent answers when evidence is missing.
- Claim support: answer claims can be traced to evidence spans.

## Regression Cases

Evaluation cases should include questions with broad shared terms such as `n8n`, `Supabase`, `API`, and `Docker`. These cases must verify that unrelated lessons are not shown as sources merely because they contain overlapping vocabulary.

## Reports

Reports should keep:

- question;
- expected document ids;
- routed document ids;
- evidence ids;
- answer status;
- shown sources;
- claim verification result.
