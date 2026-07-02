import asyncio

from app.docs_registry.activation import DocsActivationPlan, DocsActivationQualityGate, DocsActivationResult
from app.docs_registry.models import DocsCandidatePreviewResult, DocsSourceCandidate, DocsSourceCandidatesConfig
from app.docs_registry.queue import DocsActivationQueueService
from app.service_registry.types import ServiceDocsStatus


class FakePreviewService:
    def __init__(self, results: dict[str, DocsCandidatePreviewResult], errors: dict[str, Exception] | None = None) -> None:
        self.results = results
        self.errors = errors or {}
        self.calls: list[str] = []
        self.mutations: list[str] = []

    async def preview(self, service_id_or_alias: str, *, limit: int = 5) -> DocsCandidatePreviewResult:
        del limit
        self.calls.append(service_id_or_alias)
        if service_id_or_alias in self.errors:
            raise self.errors[service_id_or_alias]
        return self.results[service_id_or_alias]

    async def index(self) -> None:
        self.mutations.append("index")
        raise AssertionError("queue preview must not index")


class FakeActivationService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def activate(self, service_id_or_alias: str) -> DocsActivationResult:
        self.calls.append(service_id_or_alias)
        return DocsActivationResult(
            plan=DocsActivationPlan(
                service_id=service_id_or_alias,
                display_name=service_id_or_alias,
                docs_source=f"{service_id_or_alias}_docs",
                allowed_domains=("docs.example.com",),
                start_urls=("https://docs.example.com/",),
                max_pages=5,
                crawl_depth=1,
                risk_level="low",
                confirm_command=f"/docs_activate {service_id_or_alias} confirm",
            ),
            fetched_pages=5,
            indexed_new=5,
            chunks_total=10,
            quality_gate=DocsActivationQualityGate(passed=True),
        )


class FakeStatusProvider:
    def __init__(self, statuses: tuple[ServiceDocsStatus, ...]) -> None:
        self.statuses = statuses
        self.calls: list[dict[str, object]] = []

    async def list_statuses(self, **kwargs: object) -> tuple[ServiceDocsStatus, ...]:
        self.calls.append(kwargs)
        return self.statuses


def test_queue_preview_classifies_candidates_without_indexing() -> None:
    preview = FakePreviewService(
        {
            "openrouter": _preview("openrouter", "OpenRouter", "openrouter_docs", pages_found=5),
            "ollama": _preview("ollama", "Ollama", "ollama_docs", pages_found=5, risk_level="review"),
            "aiogram": _preview("aiogram", "aiogram", "aiogram_docs", pages_found=1),
        },
        errors={"claude_code": RuntimeError("redirect error")},
    )
    service = DocsActivationQueueService(
        candidates_config=_config(
            _candidate("openrouter", "OpenRouter", "openrouter_docs"),
            _candidate("ollama", "Ollama", "ollama_docs", risk_level="review"),
            _candidate("aiogram", "aiogram", "aiogram_docs"),
            _candidate("claude_code", "Claude Code", "claude_code_docs"),
            _candidate("supabase", "Supabase", "supabase_docs"),
        ),
        preview_service=preview,
        status_provider=FakeStatusProvider((_status("supabase", "Supabase", "supabase_docs"),)),
    )

    report = asyncio.run(service.preview_all())

    assert {item.service_id for item in report.ready} == {"openrouter"}
    assert {item.service_id for item in report.needs_review} == {"ollama", "aiogram"}
    assert {item.service_id for item in report.failed} == {"claude_code"}
    assert {item.service_id for item in report.already_connected} == {"supabase"}
    assert preview.calls == ["openrouter", "ollama", "aiogram", "claude_code"]
    assert preview.mutations == []


def test_queue_activate_ready_uses_only_ready_allowlisted_candidates() -> None:
    activation = FakeActivationService()
    service = DocsActivationQueueService(
        candidates_config=_config(
            _candidate("openrouter", "OpenRouter", "openrouter_docs"),
            _candidate("telegram_bot_api", "Telegram Bot API", "telegram_bot_api_docs"),
            _candidate("nocodb", "NocoDB", "nocodb_docs"),
            _candidate("ollama", "Ollama", "ollama_docs", risk_level="review"),
            _candidate("claude_code", "Claude Code", "claude_code_docs"),
        ),
        preview_service=FakePreviewService(
            {
                "openrouter": _preview("openrouter", "OpenRouter", "openrouter_docs", pages_found=5),
                "telegram_bot_api": _preview(
                    "telegram_bot_api",
                    "Telegram Bot API",
                    "telegram_bot_api_docs",
                    pages_found=5,
                ),
                "nocodb": _preview("nocodb", "NocoDB", "nocodb_docs", pages_found=5),
                "ollama": _preview("ollama", "Ollama", "ollama_docs", pages_found=5, risk_level="review"),
                "claude_code": _preview(
                    "claude_code",
                    "Claude Code",
                    "claude_code_docs",
                    pages_found=0,
                    status="failed",
                ),
            }
        ),
        activation_service=activation,
    )

    result = asyncio.run(service.activate_ready())

    assert activation.calls == ["openrouter", "telegram_bot_api"]
    assert {item.service_id for item in result.skipped} >= {"nocodb", "ollama", "claude_code"}
    assert len(result.activated) == 2


def test_queue_activation_plan_does_not_activate() -> None:
    activation = FakeActivationService()
    service = DocsActivationQueueService(
        candidates_config=_config(_candidate("openrouter", "OpenRouter", "openrouter_docs")),
        preview_service=FakePreviewService(
            {"openrouter": _preview("openrouter", "OpenRouter", "openrouter_docs", pages_found=5)}
        ),
        activation_service=activation,
    )

    report = asyncio.run(service.activation_plan())

    assert report.ready[0].service_id == "openrouter"
    assert activation.calls == []


def _config(*candidates: DocsSourceCandidate) -> DocsSourceCandidatesConfig:
    return DocsSourceCandidatesConfig(candidates=candidates)


def _candidate(
    service_id: str,
    display_name: str,
    docs_source: str,
    *,
    risk_level: str = "low",
) -> DocsSourceCandidate:
    return DocsSourceCandidate(
        service_id=service_id,
        display_name=display_name,
        aliases=(service_id,),
        docs_source=docs_source,
        official_start_urls=("https://docs.example.com/",),
        allowed_domains=("docs.example.com",),
        allow_patterns=(r"^https://docs\.example\.com/",),
        deny_patterns=("/login",),
        max_pages=5,
        crawl_depth=1,
        risk_level=risk_level,  # type: ignore[arg-type]
        notes="test",
    )


def _preview(
    service_id: str,
    display_name: str,
    docs_source: str,
    *,
    pages_found: int,
    pages_checked: int = 5,
    status: str = "ok",
    risk_level: str = "low",
    warnings: tuple[str, ...] = (),
) -> DocsCandidatePreviewResult:
    return DocsCandidatePreviewResult(
        service_id=service_id,
        display_name=display_name,
        docs_source=docs_source,
        allowed_domains=("docs.example.com",),
        start_urls=("https://docs.example.com/",),
        pages_checked=pages_checked,
        pages_found=pages_found,
        sample_titles=("Overview",),
        status=status,  # type: ignore[arg-type]
        warnings=warnings,
        risk_level=risk_level,  # type: ignore[arg-type]
    )


def _status(service_id: str, display_name: str, docs_source: str) -> ServiceDocsStatus:
    return ServiceDocsStatus(
        service_id=service_id,
        display_name=display_name,
        aliases=(service_id,),
        docs_source=docs_source,
        configured_status="enabled",
        docs_status="indexed",
        active_docs_count=2,
        active_chunks_count=10,
        quality_status="PASS",
        docs_source_configured=True,
    )
