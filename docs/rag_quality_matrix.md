# RAG Quality Matrix

This matrix describes expected behavior by question type. It is a design tool for future changes and eval cases. It should not be implemented as a hardcoded dispatch table.

| Question type | Expected content type | Where to search | Must not use as evidence | Expected answer mode |
| --- | --- | --- | --- | --- |
| Lesson question | Lesson explanation, steps, definitions, examples | Document cards for lesson materials, then sections/chunks inside selected lessons | Course title only, common platform term, unrelated lesson in same course | `answer_from_materials` if evidence is direct; `partial_answer` if incomplete; `out_of_base` if absent |
| Homework question | Assignment text, deliverables, constraints, examples | Homework documents and lesson sections that explicitly define the task | General lesson topic, student chat guesses, course name | `answer_from_materials` or `partial_answer` |
| Homework review rule | Rubric, checklist, acceptance criteria, allowed/forbidden solutions | Homework-check rules, rubrics, teacher notes | The submitted homework itself unless it contains the rule; course hint | `answer_from_materials`; `ask_for_missing_data` if the homework or criteria are missing |
| Course conditions | Start dates, access rules, pricing rules, support rules, deadlines | Course-condition docs, admin docs, official course policy docs | Lesson content, unrelated admin examples, old versions without active status | `answer_from_materials`; `out_of_base` if no active evidence |
| Course list | Course names, availability, ordering, modules | Course catalog or module structure docs | Similar course titles from unrelated documents | `answer_from_materials` |
| Technical error | Error meaning, likely causes, diagnostic steps, next data needed | Error writeups, technical instructions, relevant official docs if allowed | Workflow logs before the failing system is reachable; same platform but different symptom | `answer_from_materials`, `partial_answer`, or `ask_for_missing_data` |
| External documentation | Current official behavior, API parameters, version-sensitive details | Approved external-doc source flow and local docs that cite the official source | Blog posts or local guesses unless accepted by policy; docs for another subquestion | `answer_from_materials` if accepted docs evidence exists; `out_of_base` otherwise |
| Question without course | Answer based on object/action/symptom, not only course | All active document cards in workspace, narrowed by answerability | Default course assumption; platform-only match | `answer_from_materials`, `partial_answer`, or `ask_for_missing_data` |
| Question with short course hint | Use hint for routing only, then require evidence inside selected docs | Course-filtered document cards and evidence spans | The course hint itself as proof; all documents in that course | Same as the underlying task type |
| Small talk | Short friendly operational response | No RAG retrieval required | Knowledge-base sources, document candidates, fake evidence | `general_answer_without_sources` |
| Out-of-base | Honest no-evidence response | Route broadly enough to confirm absence, then no accepted evidence | Any similar chunk that does not answer the question; document title only | `out_of_base` |
| Ambiguous question | Clarifying question or partial answer from confirmed context | Dialog context, question analysis, narrow evidence if enough context exists | Guessing the missing object/course/error; unrelated history | `ask_for_missing_data` or `partial_answer` |

## Quality Expectations

- The same common word in two documents is not enough to select a source.
- The same platform in two documents is not enough to select a source.
- Routing can use broad candidates, but generation context must stay narrow.
- Sources should be fewer and stricter than retrieval candidates.
- If the expected evidence type is absent, the correct behavior is often `out_of_base` or `ask_for_missing_data`, not a longer answer.
