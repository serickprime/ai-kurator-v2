-- AI Kurator V2 schema draft.
-- This file is the canonical starting point for future Supabase migrations.
-- Do not apply it to a live project without reviewing the current schema first.

create extension if not exists vector;

create table if not exists documents (
    id uuid primary key default gen_random_uuid(),
    title text not null,
    source_uri text,
    content_hash text not null,
    status text not null default 'active',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists documents_content_hash_key
    on documents (content_hash);

create table if not exists document_versions (
    id uuid primary key default gen_random_uuid(),
    document_id uuid not null references documents(id) on delete cascade,
    version_number integer not null,
    text_hash text not null,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    unique (document_id, version_number)
);

create table if not exists document_cards (
    id uuid primary key default gen_random_uuid(),
    document_id uuid not null references documents(id) on delete cascade,
    summary text not null,
    keywords text[] not null default '{}',
    entities text[] not null default '{}',
    embedding vector(768),
    created_at timestamptz not null default now()
);

create table if not exists indexed_units (
    id uuid primary key default gen_random_uuid(),
    document_id uuid not null references documents(id) on delete cascade,
    document_version_id uuid not null references document_versions(id) on delete cascade,
    ordinal integer not null,
    text text not null,
    locator text,
    embedding vector(768),
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    unique (document_version_id, ordinal)
);

create index if not exists indexed_units_active_document_idx
    on indexed_units (document_id)
    where is_active = true;

create table if not exists conversations (
    id uuid primary key default gen_random_uuid(),
    telegram_chat_id bigint not null,
    created_at timestamptz not null default now()
);

create table if not exists messages (
    id uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references conversations(id) on delete cascade,
    telegram_message_id bigint,
    role text not null,
    text text not null,
    created_at timestamptz not null default now()
);
