"""Preview or execute exact cleanup of one incomplete external-doc draft."""

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
from app.docs_registry.incomplete_draft_cleanup import (  # noqa: E402
    IncompleteDraftCleanupError,
    build_incomplete_draft_cleanup_plan,
    execute_incomplete_draft_cleanup,
    format_incomplete_draft_cleanup_plan_text,
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Preview or execute exact cleanup of one incomplete external-doc draft.",
    )
    parser.add_argument("--service", required=True, help="Canonical service id or alias.")
    parser.add_argument("--source", help="Optional explicit source_id safety check.")
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE, help="Workspace name to inspect.")
    parser.add_argument("--backup", type=Path, required=True, help="Rollback-capable source baseline manifest.")
    parser.add_argument(
        "--document-id",
        action="append",
        default=None,
        required=True,
        help="Exact draft document UUID to clean up. Must be supplied exactly once.",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Output format.")
    parser.add_argument("--limit", type=int, default=10000, help="Max rows per read-only table query.")
    parser.add_argument(
        "--confirm-cleanup-incomplete-draft",
        action="store_true",
        help="Execute exact draft cleanup after all gates pass.",
    )
    parser.add_argument("--confirmation-phrase", default="", help="Exact phrase required with execution flag.")
    parser.add_argument("--registry-config", type=Path, default=DEFAULT_SERVICE_REGISTRY_CONFIG)
    parser.add_argument("--external-config", type=Path, default=DEFAULT_EXTERNAL_DOCS_CONFIG)
    return parser.parse_args(argv)


async def main_async() -> int:
    """Run preview or explicit exact cleanup."""
    args = parse_args()
    client: SupabaseClient | None = None
    try:
        plan, client, scope, backup_manifest, provider = await _build_live_plan(args)
        result = None
        if args.confirm_cleanup_incomplete_draft:
            repository = DocumentRepository(client)

            async def load_post_cleanup_inventory():
                return await provider.load_full_export_rows(scope.source_id)

            result = await execute_incomplete_draft_cleanup(
                plan=plan,
                repository=repository,
                load_post_cleanup_inventory=load_post_cleanup_inventory,
                scope=scope,
                backup_manifest=backup_manifest,
                confirmation_phrase=args.confirmation_phrase,
            )
        if args.format == "json":
            payload: dict[str, object] = {"plan": plan.to_dict()}
            if result is not None:
                payload["cleanup_result"] = result.to_dict()
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(format_incomplete_draft_cleanup_plan_text(plan))
            if result is not None:
                print("")
                print("Incomplete draft cleanup result")
                print(f"- status: {result.status}")
                print(f"- rows deleted: {result.rows_deleted}")
                print(f"- target absent: {'yes' if result.target_absent else 'no'}")
                print(f"- source matches baseline: {'yes' if result.source_matches_baseline else 'no'}")
                print(f"- partial failure: {'yes' if result.partial_failure else 'no'}")
        if result is not None:
            return 0 if result.status == "cleaned" else 2
        return 0 if plan.readiness else 2
    except (DocsReprocessingPlanError, IncompleteDraftCleanupError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI must not expose runtime tracebacks
        print(f"ERROR: runtime unavailable: {exc.__class__.__name__}", file=sys.stderr)
        return 2
    finally:
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
    backup_manifest = load_manifest(args.backup)
    client = SupabaseClient(settings)
    provider = DocsReprocessingRuntimeProvider(client, workspace=args.workspace, limit=args.limit)
    inventory = await provider.load_full_export_rows(scope.source_id)
    current_plan = build_reprocessing_plan(scope=scope, inventory=inventory)
    plan = build_incomplete_draft_cleanup_plan(
        scope=scope,
        inventory=inventory,
        current_plan=current_plan,
        backup_manifest=backup_manifest,
        document_ids=tuple(args.document_id or ()),
    )
    return plan, client, scope, backup_manifest, provider


def main() -> None:
    """CLI entry point."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
