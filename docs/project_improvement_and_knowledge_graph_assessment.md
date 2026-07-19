# Аудит улучшений AI Kurator V2 и оценка концепции Knowledge Graph

Дата аудита: 2026-07-19.

Статус документа: аналитика и рекомендации. Этот документ не меняет roadmap,
RAG pipeline, AnswerGenerator, retrieval/router, конфигурацию, схему Supabase или
production-данные.

Обновление после аудита: по явной команде владельца начат General Improvement
Block 1. Первый блок устраняет общий разрыв answer flow: Documentation
Discovery больше не должно заменять обычный evidence-first ответ. Это не
означает, что detection, ranking, crawl или indexing уже улучшены; такие
изменения должны выполняться отдельными проверяемыми блоками.

## Краткий вывод

AI Kurator V2 уже заметно сильнее обычного RAG. В проекте есть document-first
routing, гибридный поиск, corpus-aware term statistics, строгий Evidence Pack,
проверка утверждений, контролируемые источники и большой набор тестов. На
исходном `main` локально проходили `1034` теста; после General Improvement
Block 1 полный набор содержит `1037` проходящих тестов.

Мое честное мнение о предложенной Wiki/Knowledge Graph концепции:

- как долгосрочное направление она разумна;
- как следующий этап разработки она преждевременна;
- полезная часть идеи - навигация по связанным понятиям с обязательной
  provenance до исходного evidence;
- опасная часть идеи - автоматический граф, который становится жестким
  фильтром документов и обещает снижение стоимости без измерений;
- полноценный граф сейчас с высокой вероятностью добавит больше сложности и
  новых ошибок, чем качества.

Если оценивать отдельно, направление заслуживает примерно `7/10`, а готовность
к немедленному внедрению в текущий production path - примерно `3/10`.

Главная причина: завершенный baseline нашел `evidence_selection_gap` после
того, как нужные документы уже были выбраны. Knowledge Graph работает раньше,
на этапе навигации и routing, поэтому он не является прямым ответом на этот
измеренный сбой.

## На чем основан аудит

Проверены:

- обязательные project control docs;
- фактический `main` на коммите `02b8693`;
- схема и repository layer Supabase;
- ingestion, document cards, sections и chunks;
- question analysis, query enrichment, document routing, evidence retrieval,
  reranking, Evidence Pack, generation и ClaimVerifier;
- Telegram wiring, upload flow и conversation wiring;
- eval и no-write answer-quality harness;
- CI, зависимости и структура тестов;
- файл `D:\Serick Obsidian\Rutaror_bot\V.2\Концепция развития проекта AI Kurator.md`.

Во время первоначального аудита production Supabase не читался и не изменялся.
Crawl, sync, indexing, reindex, activation, Telegram send и внешние
model/search вызовы не выполнялись.

## Что в проекте уже хорошо

1. Evidence-first граница реализована явно.

   AnswerGenerator получает только `QuestionAnalysis`, `EvidencePack`, вопрос и
   компактный dialog context. Raw и discarded candidates не входят в контракт
   генерации, а источники строятся из принятого evidence.

2. Поиск уже многоступенчатый.

   Сначала выбираются `document_cards`, затем chunks внутри доверенного набора
   документов. Используются embeddings, PostgreSQL full-text search, trigram,
   corpus-aware редкость терминов и детерминированный reranking.

3. Ingestion уже создает часть предлагаемого Wiki Layer.

   `document_cards` содержат `summary`, `topics`, `questions_answered`,
   `entities`, `task_types`, `not_about` и embedding. `sections` уже являются
   логическими родительскими разделами с heading, summary и embedding.

4. Версионирование источников сделано аккуратно.

   Новая версия сначала создается как draft, затем индексируется, прежняя
   active-версия архивируется только перед активацией новой. Retrieval SQL
   ограничивает evidence активными документами.

5. Тестовый контур сильный.

   В репозитории `81` test-файл. Исходный полный локальный прогон завершился
   результатом `1034 passed in 29.62s`; после первого improvement-блока -
   `1037 passed`. Есть негативные retrieval-кейсы, проверки источников,
   архивных версий, no-write harness и secret scan.

Это хорошая база для развития. Проблема проекта сейчас не в отсутствии еще
одного интеллектуального слоя, а в согласованности состояния, измеримости и
стоимости сопровождения уже существующей логики.

## Приоритетные улучшения

### P0. Восстановить единое фактическое состояние проекта

На момент первоначального аудита repository docs противоречили Git и друг
другу:

- `main` уже содержит merge `phase7c-b-evidence-selection-gap`;
- `project_status.md`, `roadmap.md` и handoff все еще называют Phase 7C-B
  незапущенной;
- `agent_workflow.md`, `architecture_guardrails.md` и `prompting_playbook.md`
  местами все еще называют незапущенной Phase 7C-A;
- `docs/mvp_finish.md` показывает `14/15` и утверждает, что commit/push/merge не
  сделаны, хотя docs discovery MVP уже находится в `main`;
- один feature-коммит изменил `31` файл и добавил около `8.7k` строк, совместив
  evidence-selection fix, docs discovery, Telegram UI и schema migration.

General Improvement Block 1 синхронизирует текущий focus и честно отделяет
наличие кода в `main` от неподтвержденных production validation/migration/smoke.

Это наиболее срочная проблема для следующих агентов: инструкции могут
запретить уже завершенную работу или направить разработку не в тот блок.

Что улучшить:

- определить реальный статус Phase 7C-B по результатам post-fix baseline;
- отдельно классифицировать docs discovery MVP: merged, feature-flagged,
  migration pending/applied/unknown, manual smoke pending/completed;
- синхронизировать status, roadmap, guardrails, workflow, playbook, handoff и
  decision log одним owner-approved docs-блоком;
- не смешивать retrieval fix, новую продуктовую функцию и migration в одной
  ветке в будущем.

### P0. Доказать эффект уже внесенного evidence-selection fix

Unit-тесты подтверждают внутренние контракты, но не подтверждают, что WARN для
`n8n_docs` и `openrouter_docs` исчез на реальном корпусе без регрессий.

Нужен один owner-approved read-only повтор Phase 7C harness на том же наборе
кейсов и с сопоставимым answer mode. Следует сравнить:

- выбранные документы;
- accepted evidence и обязательные high-signal terms;
- unrelated-evidence negatives;
- service-free и different-service negatives;
- out-of-base поведение;
- sources, latency и размер контекста.

До этого нельзя честно считать текущий blocker закрытым и нельзя выбирать
Knowledge Graph как следующий способ улучшения retrieval.

### P0. Добавить измеримость latency, tokens и cost

В runtime есть model attempts и общий duration в harness, но нет сквозного
учета prompt tokens, completion tokens, стоимости, latency каждой стадии и
причины fallback/degradation.

Нужно собирать безопасную stage telemetry:

- `analysis_ms`, `routing_ms`, `retrieval_ms`, `rerank_ms`, `generation_ms`;
- число routed documents, raw chunks, accepted и discarded evidence;
- Evidence Pack characters/tokens;
- модель, provider usage и расчетная стоимость;
- fallback с векторного поиска на lexical/table path;
- sanitized trace id без текста вопроса, секретов и внутренних UUID для
  обычного пользователя.

Без этих данных утверждение концепции о снижении контекста с `15000-25000` до
`5000-8000` токенов является гипотезой. Более того, существующий synthetic
report уже показывал средний Evidence Pack около `2.13` элементов, то есть
контекст может быть компактным и без графа.

### P1. Снизить эвристический долг retrieval

Текущий retrieval качественный, но сложный: веса, пороги, русские корни,
нормализация и похожие проверки распределены между `question_analysis.py`,
`document_router.py`, `evidence_retriever.py`, `reranker.py` и
`evidence_pack.py`.

Дополнительные признаки долга:

- `QuestionAnalysis` и `QueryPlan` дублируют значительную часть полей;
- одна и та же легкая морфология реализована в нескольких модулях;
- service-specific варианты `н8н` и `нейтн` жестко нормализуются в Python,
  хотя guardrails требуют хранить такие варианты в glossary/config;
- в glossary есть вопрос-специфичная строка про отправку сообщения, которая
  добавляет `getFile` и `MessageEntity`; это требует отдельной проверки на
  соответствие intent, чтобы не расширять query нерелевантными anchors.

Что улучшить отдельным будущим блоком:

- вынести общую generic normalization/token/root logic в один модуль;
- оставить service-specific aliases и canonical anchors только в config;
- добавить semantic lint glossary: phrase, intent и anchors должны быть
  совместимы;
- калибровать веса на versioned eval, а не отдельными локальными поправками;
- добавить monotonic/invariant tests для scoring и negative cases.

### P1. Усилить Claim Verification

Текущий `ClaimVerifier` в основном проверяет пересечение корней слов между
claim и evidence. Это полезный дешевый gate, но он не умеет надежно выявлять:

- отрицание и противоречие;
- неверное число, версию или имя параметра при совпадающих терминах;
- причинно-следственную связь, отсутствующую в evidence;
- claim, составленный из частей разных chunks без общего подтверждения.

Следующий уровень должен строить `claim -> supporting evidence ids`, отдельно
проверять exact values и contradiction, а дорогой LLM/NLI verifier использовать
только для спорных claims. Любой такой этап нужно сравнивать с текущим
детерминированным verifier по false accept, false reject, latency и cost.

### P1. Сделать ingestion более наблюдаемым и идемпотентным

Draft-first процесс хорошо защищает прежнюю active-версию, но ingestion состоит
из нескольких отдельных REST writes. Сбой между card, sections, chunks и
activation оставляет частичное состояние, для которого уже понадобилось
отдельное cleanup tooling.

Knowledge extraction сделает эту цепочку длиннее. До нее полезно иметь:

- явный ingestion job/state machine;
- idempotency key и extraction/indexing version;
- структурированные partial-failure states;
- проверку полноты draft перед activation;
- понятную retry/cleanup policy;
- Phase 8A cleanup временных Telegram uploads после успеха и ошибки.

### P1. Закрыть пользовательские пробелы раньше графа

Для реального Telegram-продукта Phase 8B дает более очевидную ценность, чем
Knowledge Graph:

- follow-up вопрос сейчас не участвует в retrieval как история диалога;
- `conversations` используются частично, а сообщения не сохраняются обычным
  answer flow;
- active conversation в основном process-local;
- user settings по умолчанию остаются in-memory.

Нужна bounded conversation memory как context, но не evidence, с новой
retrieval для каждого вопроса и изоляцией пользователей.

### P2. Снизить стоимость сопровождения кода

Крупнейшие production-модули уже стали трудны для безопасного изменения:

- `app/rag/quality_harness.py` - более `2500` непустых строк;
- `app/bot/handlers.py` - более `1300`;
- `question_analysis.py`, `document_router.py`, `evidence_retriever.py` и
  несколько docs-registry модулей приближаются к `1000` строк.

Рекомендуется выделять policy, storage adapters, diagnostics, serialization и
case catalog в отдельные модули, сохраняя публичные контракты. Особенно важно
продолжать выносить business logic из Telegram handlers.

Также проекту не хватает воспроизводимого engineering toolchain:

- зависимости заданы диапазонами, lock-файла нет;
- нет `pyproject.toml` с едиными настройками;
- CI выполняет compileall, pytest, JSON check и secret scan, но не запускает
  lint, formatting check или type check.

Сначала стоит зафиксировать версии зависимостей, затем добавить быстрый Ruff и
постепенный type check для новых/изменяемых модулей, не превращая это в большой
рефакторинг всего репозитория.

## Что в Knowledge Graph концепции уже реализовано

| Идея концепции | Фактическое состояние |
|---|---|
| Логические разделы документа | Уже есть в `sections` |
| Малые searchable fragments | Уже есть в `chunks` |
| Embeddings для разных уровней | Есть для cards, sections и chunks |
| Краткая семантическая карточка | Уже есть в `document_cards` |
| Topics и entities | Уже есть массивами в document card и service metadata |
| Версии и archive | Уже есть в `documents` |
| Поиск сначала по объекту, затем по chunks | Уже есть как document-first routing |
| Canonical concepts между документами | Нет |
| Явные provenance-backed relations | Нет |
| Graph traversal для query expansion | Нет |
| Multi-hop eval, доказывающий необходимость графа | Нет |
| Измерение экономии токенов и стоимости | Нет |

Поэтому добавление `wiki_pages` и `wiki_entities` в предложенном виде будет
частично дублировать `document_cards`, `sections`, `topics` и `entities`.
Реально новым элементом является не Wiki page, а надежная связь между
canonical concept и конкретным source evidence.

## Слабые предположения исходной концепции

1. Граф не означает, что система "понимает" предметную область.

   Автоматически извлеченный граф является еще одним derived index. Он наследует
   ошибки LLM, неоднозначность терминов, устаревшие документы и ложные связи.

2. Больше связанных тем не всегда означает лучший ответ.

   Для вопроса "почему не работает match_documents" нельзя автоматически
   считать обязательными HNSW, n8n, все embeddings и весь Supabase. Сначала
   нужны конкретная ошибка, сигнатура RPC, размерность vector и место вызова.
   Остальные темы могут только вытеснить полезное evidence.

3. Жесткий prefilter по графу опасен.

   Если ошибочная Wiki page выберет не те документы, последующий retrieval
   будет искать "только внутри нужных документов" и потеряет recall. Безопаснее
   объединять текущий route с ограниченным graph-expanded route, а не заменять
   его.

4. Извлечение не является строго одноразовой операцией.

   Документы обновляются, extractor и prompt меняются, concepts объединяются и
   разделяются, active-версии архивируются. Нужны reprocessing, invalidation,
   provenance и conflict resolution.

5. Заявленная экономия не доказана.

   Graph extraction, embeddings concepts, relation resolution и graph search
   тоже стоят денег и времени. Экономия возможна только при измеренном
   уменьшении полного запроса, а не только числа chunks в финальном prompt.

6. Короткая Wiki-статья может незаметно стать псевдо-evidence.

   Даже если ее не передавать AnswerGenerator, она влияет на то, где ищется
   evidence. Поэтому каждая concept/relation должна иметь явные source links,
   confidence, version и область действия.

## Более безопасная версия идеи

Knowledge layer следует вводить как опциональный навигатор recall, а не как
новый источник и не как обязательный фильтр:

```text
question -> current document route -------------------\
                                                       -> union and dedupe
question -> optional topic navigator -> related seeds /
        -> existing scoped retrieval
        -> existing reranker
        -> existing Evidence Pack gates
        -> existing generation and verification
```

Обязательные правила такого слоя:

- graph text никогда не попадает в AnswerGenerator;
- answers и sources по-прежнему строятся только из active source evidence;
- expansion ограничен одним hop, top-k и бюджетом;
- relation применяется только в том же workspace и допустимом service/source
  scope;
- каждая relation имеет source document/section/chunk provenance;
- archived document version выключает связанные mentions/evidence links;
- текущий retrieval остается fallback и контрольной группой;
- graph expansion включается прежде всего для multi-hop/dependency вопросов,
  а не для каждого запроса;
- связанные темы не превращаются автоматически в пункты ответа, если
  пользователь их не спрашивал.

## Рекомендуемый эксперимент без новой схемы

### Этап 1. Сначала создать multi-hop eval

Подготовить небольшой versioned набор:

- прямые вопросы, где граф не нужен;
- реальные multi-hop вопросы;
- diagnostic вопросы с недостающими данными;
- одинаковые термины в разных сервисах;
- unrelated-evidence и out-of-base negatives;
- обновленная и архивная версии одного знания.

До разработки зафиксировать метрики и go/no-go критерии.

### Этап 2. Собрать read-only прототип

Использовать существующие `document_cards.topics/entities`, section headings и
service metadata. Построить локальный/in-memory topic index на synthetic или
sanitized fixtures. На этом этапе не нужны новые Supabase tables и production
writes.

Сначала достаточно deterministic co-occurrence и exact canonical aliases. LLM
relation extraction стоит добавлять только после того, как простой вариант
покажет недостаточность.

### Этап 3. Запустить shadow comparison

Для каждого eval question сравнить current route и topic-assisted route, не
передавая новый route пользователю.

Сравнивать:

- document recall@k;
- accepted-evidence recall и precision;
- forbidden/unrelated evidence leakage;
- out-of-base accuracy;
- Evidence Pack tokens;
- end-to-end latency и model cost;
- долю graph expansions, реально добавивших полезное accepted evidence.

Пример минимального go/no-go условия: topic-assisted вариант исправляет
несколько независимых multi-hop failures, не добавляет ни одного нового
forbidden-evidence failure и дает измеримое улучшение context/cost либо качества.
Если он только меняет маршрут без улучшения accepted evidence, граф не нужен.

### Этап 4. Только после успеха проектировать schema

Если эксперимент доказал пользу, лучше не создавать одновременно
`wiki_pages`, `wiki_entities` и неявные links. Более строгая модель:

- `knowledge_concepts` - canonical concept, aliases, kind, embedding, status,
  workspace и extraction version;
- `concept_mentions` - concept -> document/section/chunk/version;
- `knowledge_relations` - typed edge, direction, confidence, review status;
- `relation_evidence` - relation -> exact source spans и extraction metadata.

Нужны unique constraints, idempotent upsert, allowlisted relation types,
active/archive semantics и owner-approved migration. Отдельная graph database
на этом масштабе не требуется: adjacency tables в PostgreSQL достаточно для
bounded one-hop expansion.

### Этап 5. Включать постепенно

Сначала shadow mode, затем owner-only diagnostics, затем небольшой feature
flag. Rollback должен означать простое отключение navigator без переиндексации
основного corpus.

## Рекомендуемый порядок развития проекта

1. Отдельно синхронизировать control docs с фактическим `main` и определить
   статус merged docs discovery/migration.
2. По явному разрешению владельца повторить no-write Phase 7C baseline после
   текущего evidence-selection change.
3. Закрыть или уточнить фактический blocker по результатам, не подгоняя один
   вопрос.
4. Добавить безопасную cost/latency/stage telemetry.
5. Реализовать Phase 8A upload lifecycle как небольшой эксплуатационный блок.
6. Спроектировать Phase 8B conversation memory как пользовательскую функцию.
7. Только затем провести отдельный Knowledge Navigator spike без schema change.

## Что не стоит делать

- Не добавлять три Wiki-таблицы только потому, что они логично выглядят на
  схеме.
- Не использовать LLM-generated summary или relation как evidence.
- Не заменять текущий router жестким graph prefilter.
- Не подгружать все соседние topics в Evidence Pack.
- Не смешивать Knowledge Graph, Phase 8B, migration и retrieval tuning в одной
  ветке.
- Не оценивать успех по красивому графу или числу созданных entities.
- Не обещать экономию до измерения реальных tokens, latency и provider cost.

## Итоговая рекомендация

Сохранить идею как отдельное направление `Knowledge Navigator`, а не начинать
реализацию `Wiki Layer` в production. Сначала восстановить правдивое состояние
project docs, проверить уже внесенный Phase 7C-B fix и добавить измеримость.

Если multi-hop eval затем покажет системную проблему, построить маленький
read-only topic-assisted prototype на существующих cards/sections. Только
измеренный выигрыш в accepted evidence при сохранении negatives оправдывает
новую schema и постоянный graph layer.
