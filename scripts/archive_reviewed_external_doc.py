"""Plan or execute one reviewed external-doc archive operation."""

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
from app.docs_registry.external_doc_archive import (  # noqa: E402
    ReviewedExternalDocArchiveError,
    build_reviewed_external_doc_archive_plan,
    execute_reviewed_external_doc_archive,
    format_archive_plan_text,
    load_reviewed_artifact,
)
from app.docs_registry.reprocessing_plan import (  # noqa: E402
    DEFAULT_WORKSPACE,
    DocsReprocessingPlanError,
    DocsReprocessingRuntimeProvider,
    build_reprocessing_plan,
    load_manifest,
    resolve_source_scope,
)
from app.external_docs.config import DEFAULT_EXTERNAL_DOCS_CONFIG  # noqa: E402
from app.service_registry.config import DEFAULT_SERVICE_REGISTRY_CONFIG  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Preview or execute one reviewed external-doc archive operation.",
    )
    parser.add_argument("--service", required=True, help="Canonical service id or alias.")
    parser.add_argument("--source", help="Optional explicit source_id safety check.")
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE, help="Workspace name to inspect.")
    parser.add_argument("--review", type=Path, required=True, help="Reviewed reconciliation artifact JSON.")
    parser.add_argument("--backup", type=Path, help="Fresh rollback-capable source baseline manifest.")
    parser.add_argument("--document-id", required=True, help="Exact target document UUID.")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Output format.")
    parser.add_argument("--limit", type=int, default=10000, help="Max rows per read-only table query.")
    parser.add_argument("--confirm-archive-one", action="store_true", help="Execute the one-row archive after all gates pass.")
    parser.add_argument("--confirmation-phrase", default="", help="Exact phrase required with --confirm-archive-one.")
    parser.add_argument("--registry-config", type=Path, default=DEFAULT_SERVICE_REGISTRY_CONFIG)
    parser.add_argument("--external-config", type=Path, default=DEFAULT_EXTERNAL_DOCS_CONFIG)
    return parser.parse_args()


async def main_async() -> int:
    """Run the archive preview or explicit one-row execution."""
    args = parse_args()
    try:
        plan, client = await _build_live_plan(args)
        result = None
        if args.confirm_archive_one:
            repository = DocumentRepository(client)
            result = await execute_reviewed_external_doc_archive(
                plan=plan,
                repository=repository,
                confirmation_phrase=args.confirmation_phrase,
            )
        if args.format == "json":
            payload: dict[str, object] = {"plan": plan.to_dict()}
            if result is not None:
                payload["execution_result"] = result.to_dict()
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(format_archive_plan_text(plan))
            if result is not None:
                print("")
                print("Archive execution result")
                print(f"- status: {result.status}")
                print(f"- rows updated: {result.rows_updated}")
                print(f"- term_statistics: {result.term_statistics_status}")
                print(f"- partial failure: {'yes' if result.partial_failure else 'no'}")
        if result is not None:
            return 0 if result.status == "archived" else 2
        return 0 if plan.readiness else 2
    except (DocsReprocessingPlanError, ReviewedExternalDocArchiveError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI must not expose runtime tracebacks
        print(f"ERROR: runtime unavailable: {exc.__class__.__name__}", file=sys.stderr)
        return 2
    finally:
        client = locals().get("client")
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
    plan = build_reviewed_external_doc_archive_plan(
        scope=scope,
        inventory=inventory,
        current_plan=current_plan,
        reviewed_artifact=reviewed_artifact,
        backup_manifest=backup_manifest,
        document_id=args.document_id,
    )
    return plan, client


def main() -> None:
    """CLI entry point."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
