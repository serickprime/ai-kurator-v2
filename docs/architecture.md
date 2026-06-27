# Architecture

AI Kurator V2 separates retrieval candidates from generation evidence.

The system may cast a wide net during early retrieval, but the answer model receives only a compact evidence pack. This prevents generic shared terms from dragging unrelated lessons into the final answer.

## Main Components

- Telegram bot: receives questions and files, returns conversational answers.
- Ingestion: loads materials, extracts text, splits text into indexed units, and creates document cards.
- Supabase: stores documents, versions, document cards, indexed units, conversations, and messages.
- Question analysis: extracts routing signals from the user question.
- Document router: selects likely documents before detailed evidence retrieval.
- Evidence retriever: searches inside selected documents only.
- Evidence pack builder: keeps only compact, relevant, sourceable spans for generation.
- Answer generator: writes an answer only from the evidence pack.
- Claim verifier: checks the answer draft against the evidence pack.
- Source formatter: shows sources only from evidence used in the final answer.

## Non-Goals For The Initial Structure

- No copied legacy `services/rag.py`.
- No global raw chunk context in generation prompts.
- No answer synthesis when evidence is missing.
- No live Supabase migration is applied by this initial commit.

## Data Boundary

Chunks or indexed units can exist as internal retrieval data. They are not the generation contract.

The generation contract is:

```text
question + evidence pack -> answer draft
```

The source contract is:

```text
used evidence -> displayed sources
```
