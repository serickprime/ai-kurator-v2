-- AI Kurator V2 evidence-first RAG schema.
-- Target Supabase project: Rag_kurator_v.2.
-- Server-side bot access is expected to use SUPABASE_SERVICE_ROLE_KEY.
-- Do not expose service role keys to browser, mobile, or other client code.

set search_path = public, extensions;

create extension if not exists vector with schema extensions;
create extension if not exists pg_trgm with schema extensions;
create extension if not exists pgcrypto with schema extensions;

create table if not exists public.workspaces (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    created_at timestamptz not null default now()
);

create table if not exists public.documents (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references public.workspaces(id) on delete cascade,
    source_type text not null,
    filename text not null,
    document_key text not null,
    title text not null,
    course text,
    module text,
    lesson text,
    version int not null default 1,
    status text not null default 'active',
    content_hash text not null,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint documents_status_check check (status in ('active', 'archived', 'deleted', 'draft')),
    constraint documents_version_positive_check check (version > 0),
    constraint documents_workspace_key_version_key unique (workspace_id, document_key, version)
);

create table if not exists public.document_cards (
    id uuid primary key default gen_random_uuid(),
    document_id uuid not null references public.documents(id) on delete cascade,
    workspace_id uuid not null references public.workspaces(id) on delete cascade,
    summary text not null,
    topics text[] not null default '{}',
    questions_answered text[] not null default '{}',
    entities text[] not null default '{}',
    task_types text[] not null default '{}',
    not_about text[] not null default '{}',
    quality_score double precision,
    card_embedding vector(1024),
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint document_cards_document_unique unique (document_id),
    constraint document_cards_quality_score_check check (
        quality_score is null or (quality_score >= 0 and quality_score <= 1)
    )
);

create table if not exists public.sections (
    id uuid primary key default gen_random_uuid(),
    document_id uuid not null references public.documents(id) on delete cascade,
    workspace_id uuid not null references public.workspaces(id) on delete cascade,
    section_index int not null,
    heading text,
    summary text,
    page_start int,
    page_end int,
    metadata jsonb not null default '{}'::jsonb,
    section_embedding vector(1024),
    constraint sections_document_index_key unique (document_id, section_index)
);

create table if not exists public.chunks (
    id uuid primary key default gen_random_uuid(),
    document_id uuid not null references public.documents(id) on delete cascade,
    section_id uuid references public.sections(id) on delete set null,
    workspace_id uuid not null references public.workspaces(id) on delete cascade,
    chunk_index int not null,
    content text not null,
    content_tsv tsvector generated always as (
        to_tsvector('simple', coalesce(heading, '') || ' ' || coalesce(content, ''))
    ) stored,
    embedding vector(1024),
    token_count int,
    page int,
    heading text,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    constraint chunks_document_index_key unique (document_id, chunk_index),
    constraint chunks_token_count_check check (token_count is null or token_count >= 0)
);

create table if not exists public.term_statistics (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references public.workspaces(id) on delete cascade,
    term text not null,
    normalized_term text not null,
    document_frequency int not null default 0,
    chunk_frequency int not null default 0,
    course_frequency int not null default 0,
    first_seen_at timestamptz,
    last_seen_at timestamptz,
    examples jsonb not null default '[]'::jsonb,
    term_type_guess text not null default 'term',
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint term_statistics_workspace_term_key unique (workspace_id, normalized_term),
    constraint term_statistics_document_frequency_check check (document_frequency >= 0),
    constraint term_statistics_chunk_frequency_check check (chunk_frequency >= 0),
    constraint term_statistics_course_frequency_check check (course_frequency >= 0)
);

alter table public.term_statistics
    add column if not exists created_at timestamptz not null default now();

create table if not exists public.evidence_logs (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references public.workspaces(id) on delete cascade,
    question text not null,
    question_analysis jsonb not null default '{}'::jsonb,
    document_candidates jsonb not null default '[]'::jsonb,
    evidence_pack jsonb not null default '[]'::jsonb,
    final_answer text,
    final_sources jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now()
);

create table if not exists public.conversations (
    id uuid primary key default gen_random_uuid(),
    telegram_user_id bigint not null,
    workspace_id uuid not null references public.workspaces(id) on delete cascade,
    title text,
    summary text,
    is_active bool not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.messages (
    id uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references public.conversations(id) on delete cascade,
    telegram_user_id bigint not null,
    role text not null,
    content text not null,
    attachments jsonb not null default '[]'::jsonb,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    constraint messages_role_check check (role in ('user', 'assistant', 'system', 'tool'))
);

create table if not exists public.bot_users (
    telegram_user_id bigint primary key,
    role text not null default 'user',
    is_active bool not null default true,
    created_at timestamptz not null default now(),
    constraint bot_users_role_check check (role in ('owner', 'admin', 'user'))
);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists documents_set_updated_at on public.documents;
create trigger documents_set_updated_at
before update on public.documents
for each row execute function public.set_updated_at();

drop trigger if exists document_cards_set_updated_at on public.document_cards;
create trigger document_cards_set_updated_at
before update on public.document_cards
for each row execute function public.set_updated_at();

drop trigger if exists term_statistics_set_updated_at on public.term_statistics;
create trigger term_statistics_set_updated_at
before update on public.term_statistics
for each row execute function public.set_updated_at();

drop trigger if exists conversations_set_updated_at on public.conversations;
create trigger conversations_set_updated_at
before update on public.conversations
for each row execute function public.set_updated_at();

create index if not exists documents_workspace_status_document_key_idx
    on public.documents (workspace_id, status, document_key);
create index if not exists documents_workspace_content_hash_idx
    on public.documents (workspace_id, content_hash);
create index if not exists documents_filename_trgm_idx
    on public.documents using gin (filename gin_trgm_ops);
create index if not exists documents_title_trgm_idx
    on public.documents using gin (title gin_trgm_ops);
create index if not exists documents_metadata_gin_idx
    on public.documents using gin (metadata);

create index if not exists document_cards_workspace_document_idx
    on public.document_cards (workspace_id, document_id);
create index if not exists document_cards_topics_gin_idx
    on public.document_cards using gin (topics);
create index if not exists document_cards_entities_gin_idx
    on public.document_cards using gin (entities);
create index if not exists document_cards_metadata_gin_idx
    on public.document_cards using gin (metadata);
create index if not exists document_cards_card_embedding_hnsw_idx
    on public.document_cards using hnsw (card_embedding vector_cosine_ops)
    where card_embedding is not null;

create index if not exists sections_workspace_document_idx
    on public.sections (workspace_id, document_id);
create index if not exists sections_document_index_idx
    on public.sections (document_id, section_index);
create index if not exists sections_heading_trgm_idx
    on public.sections using gin (heading gin_trgm_ops);
create index if not exists sections_metadata_gin_idx
    on public.sections using gin (metadata);
create index if not exists sections_section_embedding_hnsw_idx
    on public.sections using hnsw (section_embedding vector_cosine_ops)
    where section_embedding is not null;

create index if not exists chunks_workspace_document_section_idx
    on public.chunks (workspace_id, document_id, section_id);
create index if not exists chunks_section_id_idx
    on public.chunks (section_id)
    where section_id is not null;
create index if not exists chunks_document_index_idx
    on public.chunks (document_id, chunk_index);
create index if not exists chunks_content_tsv_gin_idx
    on public.chunks using gin (content_tsv);
create index if not exists chunks_content_trgm_idx
    on public.chunks using gin (content gin_trgm_ops);
create index if not exists chunks_heading_trgm_idx
    on public.chunks using gin (heading gin_trgm_ops);
create index if not exists chunks_metadata_gin_idx
    on public.chunks using gin (metadata);
create index if not exists chunks_embedding_hnsw_idx
    on public.chunks using hnsw (embedding vector_cosine_ops)
    where embedding is not null;

create index if not exists term_statistics_workspace_term_idx
    on public.term_statistics (workspace_id, normalized_term);
create index if not exists term_statistics_workspace_document_frequency_idx
    on public.term_statistics (workspace_id, document_frequency desc);
create index if not exists term_statistics_workspace_chunk_frequency_idx
    on public.term_statistics (workspace_id, chunk_frequency desc);
create index if not exists term_statistics_type_idx
    on public.term_statistics (workspace_id, term_type_guess);
create index if not exists term_statistics_metadata_gin_idx
    on public.term_statistics using gin (metadata);

create index if not exists evidence_logs_workspace_created_at_idx
    on public.evidence_logs (workspace_id, created_at desc);
create index if not exists conversations_user_workspace_active_idx
    on public.conversations (telegram_user_id, workspace_id, is_active);
create index if not exists conversations_workspace_id_idx
    on public.conversations (workspace_id);
create index if not exists messages_conversation_created_at_idx
    on public.messages (conversation_id, created_at);
create index if not exists bot_users_active_role_idx
    on public.bot_users (is_active, role);

alter table public.workspaces enable row level security;
alter table public.documents enable row level security;
alter table public.document_cards enable row level security;
alter table public.sections enable row level security;
alter table public.chunks enable row level security;
alter table public.term_statistics enable row level security;
alter table public.evidence_logs enable row level security;
alter table public.conversations enable row level security;
alter table public.messages enable row level security;
alter table public.bot_users enable row level security;

grant select, insert, update, delete on table
    public.workspaces,
    public.documents,
    public.document_cards,
    public.sections,
    public.chunks,
    public.term_statistics,
    public.evidence_logs,
    public.conversations,
    public.messages,
    public.bot_users
to service_role;

revoke all on table
    public.workspaces,
    public.documents,
    public.document_cards,
    public.sections,
    public.chunks,
    public.term_statistics,
    public.evidence_logs,
    public.conversations,
    public.messages,
    public.bot_users
from anon, authenticated;

create or replace function public.refresh_term_statistics(p_workspace_id uuid)
returns int
language plpgsql
set search_path = public, pg_temp
as $$
declare
    v_count int := 0;
begin
    delete from public.term_statistics
    where workspace_id = p_workspace_id;

    with token_rows as (
        select
            d.workspace_id,
            d.id as document_id,
            c.id as chunk_id,
            d.course,
            d.filename,
            d.title,
            d.created_at,
            d.updated_at,
            (m.term_parts)[1] as raw_term
        from public.chunks c
        join public.documents d on d.id = c.document_id
        cross join lateral regexp_matches(
            coalesce(c.heading, '') || ' ' || c.content,
            '([[:alnum:]_#+.:-]{2,})',
            'g'
        ) as m(term_parts)
        where d.workspace_id = p_workspace_id
          and c.workspace_id = p_workspace_id
          and d.status = 'active'
          and coalesce(c.heading, '') !~* '(не объясняет|not about)'
    ),
    cleaned as (
        select
            workspace_id,
            document_id,
            chunk_id,
            course,
            filename,
            title,
            created_at,
            updated_at,
            lower(trim(both '.,:;!?()[]{}"''`«»' from raw_term)) as normalized_term
        from token_rows
    ),
    filtered as (
        select *
        from cleaned
        where normalized_term <> ''
          and length(normalized_term) >= 2
          and normalized_term !~ '^[0-9]+$'
          and normalized_term not in (
              'and','the','for','with','from','about','into',
              'как','что','где','куда','когда','какой','какая','какие',
              'если','или','это','этот','эта','для','при','после','перед',
              'чтобы','на','из','в','во','с','со','по','про','не','ни','ли',
              'нужно','нужен','нужна','нужны','можно','материал','источник',
              'документ','ответ','вопрос','пример'
          )
    ),
    ranked_examples as (
        select
            normalized_term,
            document_id,
            filename,
            title,
            row_number() over (
                partition by normalized_term
                order by max(updated_at) desc, title
            ) as rn
        from filtered
        group by normalized_term, document_id, filename, title
    ),
    examples as (
        select
            normalized_term,
            jsonb_agg(
                jsonb_build_object(
                    'document_id', document_id,
                    'filename', filename,
                    'title', title
                )
                order by rn
            ) filter (where rn <= 3) as examples
        from ranked_examples
        group by normalized_term
    ),
    aggregated as (
        select
            workspace_id,
            min(normalized_term) as term,
            normalized_term,
            count(distinct document_id)::int as document_frequency,
            count(distinct chunk_id)::int as chunk_frequency,
            count(distinct nullif(course, ''))::int as course_frequency,
            min(created_at) as first_seen_at,
            max(updated_at) as last_seen_at
        from filtered
        group by workspace_id, normalized_term
    )
    insert into public.term_statistics (
        workspace_id,
        term,
        normalized_term,
        document_frequency,
        chunk_frequency,
        course_frequency,
        first_seen_at,
        last_seen_at,
        examples,
        term_type_guess,
        metadata
    )
    select
        a.workspace_id,
        a.term,
        a.normalized_term,
        a.document_frequency,
        a.chunk_frequency,
        a.course_frequency,
        a.first_seen_at,
        a.last_seen_at,
        coalesce(e.examples, '[]'::jsonb),
        case
            when a.normalized_term ~ '[[:alnum:]_.-]+:[0-9]+' then 'endpoint_or_address'
            when a.normalized_term like '%\_%' escape '\' then 'identifier'
            when a.normalized_term ~ '[[:alpha:]]+[0-9]+|[0-9]+[[:alpha:]]+' then 'technical_identifier'
            when a.normalized_term like '%.%' then 'path_or_parameter'
            else 'term'
        end as term_type_guess,
        jsonb_build_object('source', 'refresh_term_statistics')
    from aggregated a
    left join examples e on e.normalized_term = a.normalized_term;

    get diagnostics v_count = row_count;
    return v_count;
end;
$$;

create or replace function public.match_document_cards(
    p_workspace_id uuid,
    p_query_embedding vector(1024),
    p_match_count int default 10,
    p_metadata_filter jsonb default '{}'::jsonb
)
returns table (
    document_id uuid,
    filename text,
    title text,
    course text,
    lesson text,
    summary text,
    score double precision
)
language sql
stable
set search_path = public, extensions
as $$
    select
        d.id as document_id,
        d.filename,
        d.title,
        d.course,
        d.lesson,
        dc.summary,
        1 - (dc.card_embedding <=> p_query_embedding) as score
    from public.document_cards dc
    join public.documents d on d.id = dc.document_id
    where dc.workspace_id = p_workspace_id
      and d.workspace_id = p_workspace_id
      and d.status = 'active'
      and p_query_embedding is not null
      and dc.card_embedding is not null
      and (
          p_metadata_filter is null
          or p_metadata_filter = '{}'::jsonb
          or dc.metadata @> p_metadata_filter
      )
    order by dc.card_embedding <=> p_query_embedding
    limit least(greatest(coalesce(p_match_count, 10), 1), 100);
$$;

create or replace function public.match_sections(
    p_workspace_id uuid,
    p_document_ids uuid[],
    p_query_embedding vector(1024),
    p_match_count int default 20
)
returns table (
    section_id uuid,
    document_id uuid,
    heading text,
    summary text,
    page_start int,
    page_end int,
    score double precision
)
language sql
stable
set search_path = public, extensions
as $$
    select
        s.id as section_id,
        s.document_id,
        s.heading,
        s.summary,
        s.page_start,
        s.page_end,
        1 - (s.section_embedding <=> p_query_embedding) as score
    from public.sections s
    join public.documents d on d.id = s.document_id
    where s.workspace_id = p_workspace_id
      and d.workspace_id = p_workspace_id
      and d.status = 'active'
      and coalesce(cardinality(p_document_ids), 0) > 0
      and s.document_id = any(p_document_ids)
      and p_query_embedding is not null
      and s.section_embedding is not null
    order by s.section_embedding <=> p_query_embedding
    limit least(greatest(coalesce(p_match_count, 20), 1), 100);
$$;

create or replace function public.match_chunks_in_documents(
    p_workspace_id uuid,
    p_document_ids uuid[],
    p_query_embedding vector(1024) default null,
    p_query_text text default null,
    p_match_count int default 20
)
returns table (
    chunk_id uuid,
    document_id uuid,
    section_id uuid,
    content text,
    page int,
    heading text,
    vector_score double precision,
    text_score double precision,
    trigram_score double precision,
    score double precision
)
language sql
stable
set search_path = public, extensions
as $$
    with query_input as (
        select
            nullif(trim(coalesce(p_query_text, '')), '') as query_text,
            case
                when nullif(trim(coalesce(p_query_text, '')), '') is null then null::tsquery
                else websearch_to_tsquery('simple', p_query_text)
            end as query_tsq
    ),
    scored as (
        select
            c.id as chunk_id,
            c.document_id,
            c.section_id,
            c.content,
            c.page,
            c.heading,
            case
                when p_query_embedding is null or c.embedding is null then 0::double precision
                else greatest(0::double precision, 1 - (c.embedding <=> p_query_embedding))
            end as vector_score,
            case
                when q.query_tsq is null then 0::double precision
                else ts_rank_cd(c.content_tsv, q.query_tsq)::double precision
            end as text_score,
            case
                when q.query_text is null then 0::double precision
                else greatest(
                    similarity(c.content, q.query_text),
                    similarity(coalesce(c.heading, ''), q.query_text)
                )::double precision
            end as trigram_score
        from public.chunks c
        join public.documents d on d.id = c.document_id
        cross join query_input q
        where c.workspace_id = p_workspace_id
          and d.workspace_id = p_workspace_id
          and d.status = 'active'
          and coalesce(cardinality(p_document_ids), 0) > 0
          and c.document_id = any(p_document_ids)
          and (
              (p_query_embedding is not null and c.embedding is not null)
              or (
                  q.query_text is not null
                  and (
                      c.content_tsv @@ q.query_tsq
                      or c.content % q.query_text
                      or coalesce(c.heading, '') % q.query_text
                  )
              )
          )
    )
    select
        chunk_id,
        document_id,
        section_id,
        content,
        page,
        heading,
        vector_score,
        text_score,
        trigram_score,
        greatest(vector_score, text_score, trigram_score) as score
    from scored
    order by score desc, vector_score desc, text_score desc, trigram_score desc, chunk_id
    limit least(greatest(coalesce(p_match_count, 20), 1), 200);
$$;

create or replace function public.hybrid_match_chunks_in_documents(
    p_workspace_id uuid,
    p_document_ids uuid[],
    p_query_embedding vector(1024) default null,
    p_query_text text default null,
    p_match_count int default 20,
    p_vector_weight double precision default 0.60,
    p_text_weight double precision default 0.30,
    p_trigram_weight double precision default 0.10
)
returns table (
    chunk_id uuid,
    document_id uuid,
    section_id uuid,
    content text,
    page int,
    heading text,
    vector_score double precision,
    text_score double precision,
    trigram_score double precision,
    score double precision
)
language sql
stable
set search_path = public, extensions
as $$
    with matches as (
        select *
        from public.match_chunks_in_documents(
            p_workspace_id,
            p_document_ids,
            p_query_embedding,
            p_query_text,
            least(greatest(coalesce(p_match_count, 20), 1) * 4, 400)
        )
    )
    select
        chunk_id,
        document_id,
        section_id,
        content,
        page,
        heading,
        vector_score,
        text_score,
        trigram_score,
        (
            vector_score * greatest(coalesce(p_vector_weight, 0), 0)
            + text_score * greatest(coalesce(p_text_weight, 0), 0)
            + trigram_score * greatest(coalesce(p_trigram_weight, 0), 0)
        ) as score
    from matches
    order by score desc, vector_score desc, text_score desc, trigram_score desc, chunk_id
    limit least(greatest(coalesce(p_match_count, 20), 1), 200);
$$;

revoke execute on function public.set_updated_at() from public, anon, authenticated;
revoke execute on function public.refresh_term_statistics(uuid) from public, anon, authenticated;
revoke execute on function public.match_document_cards(uuid, vector, int, jsonb) from public, anon, authenticated;
revoke execute on function public.match_sections(uuid, uuid[], vector, int) from public, anon, authenticated;
revoke execute on function public.match_chunks_in_documents(uuid, uuid[], vector, text, int) from public, anon, authenticated;
revoke execute on function public.hybrid_match_chunks_in_documents(uuid, uuid[], vector, text, int, double precision, double precision, double precision) from public, anon, authenticated;

grant execute on function public.refresh_term_statistics(uuid) to service_role;
grant execute on function public.match_document_cards(uuid, vector, int, jsonb) to service_role;
grant execute on function public.match_sections(uuid, uuid[], vector, int) to service_role;
grant execute on function public.match_chunks_in_documents(uuid, uuid[], vector, text, int) to service_role;
grant execute on function public.hybrid_match_chunks_in_documents(uuid, uuid[], vector, text, int, double precision, double precision, double precision) to service_role;
