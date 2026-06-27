# Supabase match_documents

Материал отвечает на вопрос: как сделать RPC `match_documents` для поиска по chunks в Supabase с pgvector.

## Назначение RPC

`match_documents` должна искать по таблице `chunks`, а не по сырому полю `documents.content`.
Для evidence-first RAG функция используется только внутри выбранных документов или как часть контролируемого retrieval.

## Безопасный каркас

Функция принимает workspace id, query embedding, список document ids и match count.
Она возвращает chunk id, document id, content, heading, page и score.

```sql
select
  chunks.id,
  chunks.document_id,
  chunks.content,
  chunks.heading,
  chunks.page,
  1 - (chunks.embedding <=> p_query_embedding) as score
from public.chunks
where chunks.workspace_id = p_workspace_id
  and chunks.document_id = any(p_document_ids)
order by chunks.embedding <=> p_query_embedding
limit p_match_count;
```

## Важное ограничение

Размерность embedding должна совпадать со схемой. Для ai-kurator-v2 используется `vector(1024)` и модель BGE-M3.

## Не про платежи

Этот материал не объясняет YooMoney hash, Docker WSL или локальную установку n8n.
