# RAG Change Checklist

Use this checklist before changing RAG behavior, prompts, routing, indexing, evals, or Telegram answer handling.

## Universal Logic

- Does this improve a reusable RAG layer, or only one case?
- Would the change still make sense if the question were about homework, course rules, recipes, plants, API docs, or admin policy?
- Is there any hardcoded rule for one product, one service, one error text, or one eval id?
- Did a benchmark get better only because the implementation learned its expected answers?

## Evidence Boundaries

- Did raw candidates stay out of the generation prompt?
- Did discarded candidates stay out of the generation prompt?
- Did document candidates stay out of the generation prompt?
- Are sources built only from `EvidencePack.source_matches`?
- Are course hints and domain hints used only for routing, not as evidence?
- Are common terms treated as weak signals, not proof?
- Are partial evidence spans excluded from sources unless explicitly marked as source?

## Content-Type Boundaries

- Did `course_catalog` avoid replacing `lesson_material`?
- Did homework rules avoid being mixed with homework submissions?
- Did external documentation avoid replacing local course materials when the user asks about course content?
- Did official docs support only the subquestion they actually cover?
- Did old or archived versions avoid becoming active evidence?

## Answer Modes

- Is `answer_from_materials` used only when evidence covers the answer?
- Is `partial_answer` used when evidence covers only part of the required points?
- Is `ask_for_missing_data` used when the user omitted required input?
- Is `general_answer_without_sources` used for source-free small talk or general non-material answers?
- Is `out_of_base` used when the knowledge base has no accepted evidence?
- Are sources empty for `ask_for_missing_data`, `general_answer_without_sources`, and `out_of_base`?

## Verification

- Does claim verification remove or soften unsupported claims?
- Does the answer avoid invented commands, SQL, API parameters, node settings, course dates, deadlines, and source names?
- Does debug output help inspect the process without leaking secrets?
- Did tests cover the contract rather than memorizing one answer?

## Required Checks

Run before committing:

```powershell
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe -m compileall app scripts tests
cd D:\Downloads\ai-kurator-v2; .\.venv\Scripts\python.exe -m pytest
```
