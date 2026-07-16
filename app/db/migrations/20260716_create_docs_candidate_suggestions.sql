-- Add persistent owner-review queue for discovered documentation candidates.
-- Apply manually after owner approval:
-- psql "$SUPABASE_DB_URL" -f app/db/migrations/20260716_create_docs_candidate_suggestions.sql

set search_path = public, extensions;

create table if not exists public.docs_candidate_suggestions (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references public.workspaces(id) on delete cascade,
    service_id text not null,
    display_name text not null,
    aliases text[] not null default '{}',
    official_url text not null,
    allowed_domain text not null,
    source_query text,
    discovery_reason text,
    confidence double precision not null default 0,
    risk_level text not null default 'review',
    status text not null default 'pending',
    preview_status text not null default 'not_run',
    preview_result jsonb not null default '{}'::jsonb,
    requested_by_user_id bigint,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    reviewed_at timestamptz,
    reviewed_by_user_id bigint,
    rejection_reason text,
    metadata jsonb not null default '{}'::jsonb,
    constraint docs_candidate_suggestions_status_check check (
        status in ('pending', 'preview_ready', 'approved', 'rejected', 'failed', 'activated')
    ),
    constraint docs_candidate_suggestions_preview_status_check check (
        preview_status in ('not_run', 'ok', 'failed', 'needs_review')
    ),
    constraint docs_candidate_suggestions_risk_level_check check (
        risk_level in ('low', 'medium', 'review')
    ),
    constraint docs_candidate_suggestions_confidence_check check (
        confidence >= 0 and confidence <= 1
    )
);

drop trigger if exists docs_candidate_suggestions_set_updated_at on public.docs_candidate_suggestions;
create trigger docs_candidate_suggestions_set_updated_at
before update on public.docs_candidate_suggestions
for each row execute function public.set_updated_at();

create unique index if not exists docs_candidate_suggestions_workspace_service_url_key
    on public.docs_candidate_suggestions (
        workspace_id,
        regexp_replace(lower(btrim(service_id)), '[^a-z0-9]+', '_', 'g'),
        lower(regexp_replace(btrim(official_url), '/+$', '', 'g'))
    );
create index if not exists docs_candidate_suggestions_workspace_status_idx
    on public.docs_candidate_suggestions (workspace_id, status, updated_at desc);
create index if not exists docs_candidate_suggestions_metadata_gin_idx
    on public.docs_candidate_suggestions using gin (metadata);

alter table public.docs_candidate_suggestions enable row level security;

grant select, insert, update, delete on table public.docs_candidate_suggestions to service_role;
revoke all on table public.docs_candidate_suggestions from anon, authenticated;
