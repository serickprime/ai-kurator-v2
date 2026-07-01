# Telegram UX

The Telegram layer is intentionally thin: it prepares user intake and settings, then calls the existing RAG v2 pipeline. It does not route documents, build evidence packs, verify claims, or change the Supabase RAG schema.

## Main Menu

The persistent reply keyboard has exactly three user-facing actions:

- `Новая тема`
- `Загрузить материал`
- `Настройки`

Commands stay available:

```text
/start
/help
/status
/materials
/services
/base_status
/debug_last
/new
/upload
/done
```

Read-only status commands:

- `/services` shows services detected in the indexed base and whether their documentation source is connected.
- `/base_status` shows compact knowledge base counts, external docs source status, service status, and recent uploads.

These commands do not start sync, do not crawl the internet, and do not mutate the database.

`Новая тема` clears the local intake buffer, resets upload/follow-up state, and closes the active conversation when a conversation repository is wired.

`Загрузить материал` switches the user into `upload_material` mode. Files and images in this mode are sent to ingestion. Plain text in this mode is never sent to RAG.

Files outside upload mode are not indexed automatically. The bot asks the user to enter upload mode first.

## Settings

The inline settings menu controls:

- answer mode: `free`, `cheap`, `quality`
- vision mode: `auto`, `off`
- debug mode: `on`, `off`

The current code includes an in-memory settings fallback for tests and local dry runs, plus a Supabase repository adapter for persistent settings after the optional migration below is approved.

## Model Routing

Model lists are configured with environment variables:

```text
OPENROUTER_DEFAULT_MODEL=
OPENROUTER_VISION_MODEL=
OPENROUTER_FREE_TEXT_MODELS=
OPENROUTER_FREE_VISION_MODELS=
OPENROUTER_CHEAP_TEXT_MODELS=
OPENROUTER_CHEAP_VISION_MODELS=
OPENROUTER_QUALITY_TEXT_MODELS=
OPENROUTER_QUALITY_VISION_MODELS=
ALLOW_QUALITY_TO_CHEAP_FALLBACK=false
```

Rules:

- `free` mode only tries free models and never silently switches to paid models.
- `cheap` mode tries only the cheap list.
- `quality` mode tries quality models; fallback to cheap models happens only when `ALLOW_QUALITY_TO_CHEAP_FALLBACK=true`.
- routing metadata records attempted models, failures, successful model, provider errors, and degraded quality.

## Multimodal Intake

Telegram images are downloaded to `data/uploads/telegram/<telegram_user_id>/`.

When `vision_mode=auto`, image text is extracted and added as context to the same `UserIntake` as the user's text. Vision text is not treated as a separate question. If the user sends only an image without a caption or follow-up text, the bot asks for a concrete question instead of calling RAG.

When `vision_mode=off`, the intake records that an image exists but does not call the vision model.

## Optional Migration Proposal

Persistent user settings need one minimal table. This migration has not been applied in this step because it is a schema change and should be approved first.

```sql
create table if not exists public.user_settings (
  telegram_user_id bigint primary key references public.bot_users(telegram_user_id) on delete cascade,
  answer_mode text not null default 'cheap' check (answer_mode in ('free', 'cheap', 'quality')),
  vision_mode text not null default 'auto' check (vision_mode in ('auto', 'off')),
  debug_mode boolean not null default false,
  selected_workspace_id uuid references public.workspaces(id) on delete set null,
  updated_at timestamptz not null default now()
);

alter table public.user_settings enable row level security;

create index if not exists user_settings_workspace_idx
  on public.user_settings(selected_workspace_id);
```

Why this table is useful:

- settings survive bot restarts;
- model routing can be consistent per Telegram user;
- selected workspace can be changed later without touching RAG tables;
- RLS can stay enabled while the server bot uses `SUPABASE_SERVICE_ROLE_KEY`.

Never expose `SUPABASE_SERVICE_ROLE_KEY` in Telegram messages, frontend code, browser/mobile apps, logs, or committed files.
