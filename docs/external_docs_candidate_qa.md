# External Docs Candidate QA

## Purpose

Этот документ фиксирует ручную проверку `/docs_preview <service>` перед любым подключением external docs. Preview не индексирует документы, не пишет в Supabase и не активирует source.

## Summary

| Candidate | Status | Pages found | Risk | Decision | Notes |
|---|---:|---:|---|---|---|
| OpenRouter | можно проверить | 5 | low | ready_for_activation_candidate | Preview found expected docs pages. |
| Ollama | нужна ручная проверка | 5 | review | needs_manual_review | Preview works, but risk level requires manual review. |
| Dokploy | нужна ручная проверка | 5 | review | needs_manual_review | Preview works, but risk level requires manual review. |
| Telegram Bot API | можно проверить | 5 | low | ready_for_activation_candidate | Preview found expected official docs pages. |
| aiogram | можно проверить | 1 | low | partial_preview_needs_link_check | Preview works, but only 1 page was found; check crawl/link extraction before activation. |
| Claude Code | не удалось проверить | 0 | medium | blocked_needs_url_or_redirect_fix | Do not activate before fixing start URL or redirect handling. |

## Manual Results

### OpenRouter

Command:

```text
/docs_preview openrouter
```

Result:

- status: можно проверить
- domain: openrouter.ai
- start URL: https://openrouter.ai/docs
- pages checked: 5
- pages found: 5
- examples:
  - OpenRouter Quickstart Guide | Developer Documentation | OpenRouter | Documentation
  - OpenRouter API Reference | Complete API Documentation | OpenRouter | Documentation
  - Client SDKs | OpenRouter Documentation | OpenRouter | Documentation
- risk: low
- decision: ready_for_activation_candidate

### Ollama

Command:

```text
/docs_preview ollama
```

Result:

- status: нужна ручная проверка
- domain: docs.ollama.com
- start URL: https://docs.ollama.com/
- pages checked: 5
- pages found: 5
- examples:
  - Ollama documentation - Ollama
  - Quickstart - Ollama
  - Cloud - Ollama
  - Streaming - Ollama
  - Thinking - Ollama
- warning: нужна ручная проверка
- risk: review
- decision: needs_manual_review

### Dokploy

Command:

```text
/docs_preview dokploy
```

Result:

- status: нужна ручная проверка
- domain: docs.dokploy.com
- start URL: https://docs.dokploy.com/
- pages checked: 5
- pages found: 5
- examples:
  - Welcome to Dokploy | Dokploy
  - Architecture of Dokploy | Dokploy
  - Features | Dokploy
  - Comparison | Dokploy
- warning: нужна ручная проверка
- risk: review
- decision: needs_manual_review

### Telegram Bot API

Command:

```text
/docs_preview telegram_bot_api
```

Result:

- status: можно проверить
- domain: core.telegram.org
- start URL: https://core.telegram.org/bots/api
- pages checked: 5
- pages found: 5
- examples:
  - Telegram Bot API
  - Bots: An introduction for developers
  - Bots FAQ
  - Telegram Bot Features
  - Telegram Mini Apps
- risk: low
- decision: ready_for_activation_candidate

### aiogram

Command:

```text
/docs_preview aiogram
```

Result:

- status: можно проверить
- domain: docs.aiogram.dev
- start URL: https://docs.aiogram.dev/
- pages checked: 5
- pages found: 1
- examples:
  - aiogram 3.29.1 documentation Contents Menu Expand Light mode Dark mode Auto light/dark, in light mode Auto light/dark, in dark mode
- risk: low
- decision: partial_preview_needs_link_check
- note: preview works, but only 1 page was found; check crawl/link extraction before activation.

### Claude Code

Command:

```text
/docs_preview claude_code
```

Result:

- status: не удалось проверить
- domain: docs.anthropic.com
- start URL: https://docs.anthropic.com/en/docs/claude-code
- pages checked: 5
- pages found: 0
- warning: ошибка загрузки: Exceeded maximum allowed redirects.
- risk: medium
- decision: blocked_needs_url_or_redirect_fix
- note: do not activate before fixing start URL or redirect handling.

## Ready For Next Activation Experiment

- OpenRouter
- Telegram Bot API

Important: ready here means ready for the next controlled activation experiment, not automatic connection.

## Needs Review Before Activation

- Ollama
- Dokploy
- aiogram
- Claude Code

## Recommended Next Step

Next small PR: controlled activation flow for one low-risk candidate, preferably OpenRouter.

Rules for that PR:

- activation must be a separate PR;
- owner/admin only;
- only one candidate;
- first use pending/indexing/quality gate;
- do not bulk-connect all candidates.

## OpenRouter Activation MVP

The first controlled activation command is:

```text
/docs_activate openrouter
```

Without `confirm`, it only shows a plan and does not crawl, index, write to Supabase, or activate docs.

```text
/docs_activate openrouter confirm
```

With `confirm`, owner/admin can run the controlled activation flow for OpenRouter only. Arbitrary URLs and all other candidates are rejected in this MVP.
