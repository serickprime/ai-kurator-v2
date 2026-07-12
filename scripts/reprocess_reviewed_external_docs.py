"""Preview or execute reviewed exact-key external-doc reprocessing."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.db.repositories import DocumentRepository  # noqa: E402
from app.db.supabase_client import SupabaseClient  # noqa: E402
from app.docs_registry.external_doc_archive import load_reviewed_artifact  # noqa: E402
from app.docs_registry.reprocessing_plan import (  # noqa: E402
    DEFAULT_WORKSPACE,
    DocsReprocessingPlanError,
    DocsReprocessingRuntimeProvider,
    build_reprocessing_plan,
    load_manifest,
    resolve_source_scope,
)
from app.docs_registry.reviewed_key_reprocessing import (  # noqa: E402
    NoTermStatisticsRefreshRepository,
    ReviewedExternalDocsReprocessingError,
    build_reviewed_external_docs_reprocessing_plan,
    execute_reviewed_external_docs_reprocessing,
    format_reprocessing_plan_text,
)
from app.external_docs.config import DEFAULT_EXTERNAL_DOCS_CONFIG  # noqa: E402
from app.external_docs.crawler import ExternalDocsCrawler  # noqa: E402
from app.external_docs.extractor import ExternalDocsExtractor  # noqa: E402
from app.external_docs.indexer import ExternalDocsIndexer  # noqa: E402
from app.external_docs.types import ExternalDocSource  # noqa: E402
from app.llm.embeddings import OllamaEmbeddingClient  # noqa: E402
from app.service_registry.config import DEFAULT_SERVICE_REGISTRY_CONFIG  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Preview or execute reviewed exact-key external-doc reprocessing.",
    )
    parser.add_argument("--service", required=True, help="Canonical service id or alias.")
    parser.add_argument("--source", help="Optional explicit source_id safety check.")
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE, help="Workspace name to inspect.")
    parser.add_argument("--review", type=Path, required=True, help="Reviewed reconciliation artifact JSON.")
    parser.add_argument("--backup", type=Path, help="Fresh rollback-capable source baseline manifest.")
    parser.add_argument(
        "--document-id",
        action="append",
        default=None,
        help="Exact active document UUID to reprocess. Repeat for each reviewed target.",
    )
    parser.add_argument("--max-targets", type=int, default=2, help="Maximum exact reviewed targets allowed.")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Output format.")
    parser.add_argument("--limit", type=int, default=10000, help="Max rows per read-only table query.")
    parser.add_argument(
        "--confirm-reprocess-reviewed",
        action="store_true",
        help="Execute exact reviewed reprocessing after all gates pass.",
    )
    confirmation_group = parser.add_mutually_exclusive_group()
    confirmation_group.add_argument(
        "--confirmation-phrase",
        default="",
        help=(
            "Exact phrase required with execution flag. Legacy option: the value may be visible "
            "in the process command line; use --confirmation-phrase-stdin for monitored execution."
        ),
    )
    confirmation_group.add_argument(
        "--confirmation-phrase-stdin",
        action="store_true",
        help="Read the exact execution confirmation phrase from one stdin line.",
    )
    parser.add_argument("--registry-config", type=Path, default=DEFAULT_SERVICE_REGISTRY_CONFIG)
    parser.add_argument("--external-config", type=Path, default=DEFAULT_EXTERNAL_DOCS_CONFIG)
    return parser.parse_args(argv)


async def main_async() -> int:
    """Run preview or explicit exact-key reprocessing."""
    args = parse_args()
    client: SupabaseClient | None = None
    embedding_client: OllamaEmbeddingClient | None = None
    crawler: ExternalDocsCrawler | None = None
    try:
        plan, client, source = await _build_live_plan(args)
        result = None
        confirmation_source = "none"
        confirmation_accepted = False
        if args.confirm_reprocess_reviewed:
            confirmation_phrase, confirmation_source = _read_execution_confirmation(args)
            repository = DocumentRepository(client)
            embedding_client = OllamaEmbeddingClient(get_settings())
            crawler = ExternalDocsCrawler()
            indexer = ExternalDocsIndexer(
                repository=NoTermStatisticsRefreshRepository(repository),
                embedding_client=embedding_client,
            )
            result = await execute_reviewed_external_docs_reprocessing(
                plan=plan,
                fetcher=crawler,
                extractor=ExternalDocsExtractor(),
                indexer=indexer,
                term_repository=repository,
                confirmation_phrase=confirmation_phrase,
                source=source,
            )
            confirmation_accepted = "confirmation_phrase_mismatch" not in result.blockers
        if args.format == "json":
            include_phrase = not args.confirm_reprocess_reviewed
            payload: dict[str, object] = {"plan": plan.to_dict(include_confirmation_phrase=include_phrase)}
            if result is not None:
                payload["execution_result"] = result.to_dict()
                payload["confirmation"] = {
                    "source": confirmation_source,
                    "accepted": confirmation_accepted,
                }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            include_phrase = not args.confirm_reprocess_reviewed
            print(format_reprocessing_plan_text(plan, include_confirmation_phrase=include_phrase))
            if result is not None:
                print("")
                print("Reviewed reprocessing execution result")
                print(f"- confirmation source: {confirmation_source}")
                print(f"- confirmation accepted: {'yes' if confirmation_accepted else 'no'}")
                print(f"- status: {result.status}")
                print(f"- changed keys: {len(result.changed_keys)}")
                print(f"- failed keys: {len(result.failed_keys)}")
                print(f"- term_statistics: {result.term_statistics_status}")
                print(f"- partial failure: {'yes' if result.partial_failure else 'no'}")
        if result is not None:
            return 0 if result.status == "reprocessed" else 2
        return 0 if plan.readiness else 2
    except (DocsReprocessingPlanError, ReviewedExternalDocsReprocessingError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI must not expose runtime tracebacks
        print(f"ERROR: runtime unavailable: {exc.__class__.__name__}", file=sys.stderr)
        return 2
    finally:
        if crawler is not None:
            await crawler.close()
        if embedding_client is not None:
            await embedding_client.close()
        if client is not None:
            await client.close()


async def _build_live_plan(args: argparse.Namespace):
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise DocsReprocessingPlanError("runtime unavailable: Supabase settings are not configured")

    scope = resolve_source_scope(
        args.service,
        source_id=args.source,
        registry_config_path=args.registry_config,
        external_config_path=args.external_config,
    )
    reviewed_artifact = load_reviewed_artifact(args.review)
    backup_manifest = load_manifest(args.backup) if args.backup else None
    client = SupabaseClient(settings)
    provider = DocsReprocessingRuntimeProvider(client, workspace=args.workspace, limit=args.limit)
    inventory = await provider.load_inventory(scope.source_id)
    current_plan = build_reprocessing_plan(scope=scope, inventory=inventory, health_report=None)
    plan = build_reviewed_external_docs_reprocessing_plan(
        scope=scope,
        inventory=inventory,
        current_plan=current_plan,
        reviewed_artifact=reviewed_artifact,
        backup_manifest=backup_manifest,
        document_ids=tuple(args.document_id or ()),
        max_target_count=args.max_targets,
    )
    source = _source_from_scope(scope)
    return plan, client, source


def _read_execution_confirmation(args: argparse.Namespace) -> tuple[str, str]:
    """Return an execution confirmation phrase and its source without echoing it."""
    if args.confirmation_phrase_stdin:
        raw = sys.stdin.readline()
        if raw == "":
            raise ReviewedExternalDocsReprocessingError("confirmation_phrase_stdin_empty")
        phrase = raw.rstrip("\r\n")
        if not phrase:
            raise ReviewedExternalDocsReprocessingError("confirmation_phrase_stdin_empty")
        return phrase, "stdin"
    if args.confirmation_phrase:
        return str(args.confirmation_phrase), "argument"
    raise ReviewedExternalDocsReprocessingError("confirmation_phrase_required")


def _source_from_scope(scope) -> ExternalDocSource:
    config = scope.source_config
    return ExternalDocSource(
        name=scope.source_id,
        source_kind=str(config.get("source_kind") or "external_docs"),
        allowed_domains=tuple(str(item) for item in config.get("allowed_domains", ()) or ()),
        start_urls=tuple(str(item) for item in config.get("start_urls", ()) or ()),
        allow_patterns=tuple(str(item) for item in config.get("allow_patterns", ()) or ()),
        deny_patterns=tuple(str(item) for item in config.get("deny_patterns", ()) or ()),
        crawl_depth=0,
        max_pages=int(config.get("max_pages") or 1),
        refresh_days=int(config.get("refresh_days") or 14),
    )


def main() -> None:
    """CLI entry point."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
