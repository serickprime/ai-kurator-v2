"""Read-only source-scoped docs reconciliation planning."""

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
from app.db.supabase_client import SupabaseClient  # noqa: E402
from app.docs_registry.reconciliation_plan import (  # noqa: E402
    DocsReconciliationPlanError,
    build_reconciliation_plan,
    build_review_export,
    format_reconciliation_plan_text,
    load_snapshot,
    write_json_manifest_atomic,
)
from app.docs_registry.reprocessing_plan import (  # noqa: E402
    DEFAULT_WORKSPACE,
    DocsReprocessingRuntimeProvider,
    resolve_source_scope,
)
from app.external_docs.config import DEFAULT_EXTERNAL_DOCS_CONFIG  # noqa: E402
from app.service_registry.config import DEFAULT_SERVICE_REGISTRY_CONFIG  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Plan read-only document-key reconciliation from a discovered snapshot.",
    )
    parser.add_argument("--service", required=True, help="Canonical service id or alias, for example openrouter.")
    parser.add_argument("--source", help="Optional explicit source_id safety check.")
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE, help="Workspace name to inspect.")
    parser.add_argument("--snapshot", type=Path, required=True, help="Local discovered-key snapshot JSON.")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Output format.")
    parser.add_argument("--limit", type=int, default=10000, help="Max rows per read-only table query.")
    parser.add_argument("--review-export", type=Path, help="Write a local owner-review plan outside the repository.")
    parser.add_argument("--force", action="store_true", help="Allow overwriting an existing review export path.")
    parser.add_argument("--registry-config", type=Path, default=DEFAULT_SERVICE_REGISTRY_CONFIG)
    parser.add_argument("--external-config", type=Path, default=DEFAULT_EXTERNAL_DOCS_CONFIG)
    return parser.parse_args()


async def main_async() -> int:
    """Run read-only reconciliation planning."""
    args = parse_args()
    try:
        plan = await _build_plan(args)
        if args.review_export:
            review = build_review_export(plan)
            write_json_manifest_atomic(review, args.review_export, force=args.force)
        if args.format == "json":
            payload = {"plan": plan.to_dict()}
            if args.review_export:
                payload["review_export"] = str(args.review_export)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(format_reconciliation_plan_text(plan))
            if args.review_export:
                print("")
                print(f"Review plan exported: {args.review_export}")
                print("Supabase writes: disabled")
                print("Automatic archive: disabled")
        return 0 if plan.readiness else 2
    except DocsReconciliationPlanError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI must not expose runtime tracebacks
        print(f"ERROR: runtime unavailable: {exc.__class__.__name__}", file=sys.stderr)
        return 2


async def _build_plan(args: argparse.Namespace):
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise DocsReconciliationPlanError("runtime unavailable: Supabase settings are not configured")

    scope = resolve_source_scope(
        args.service,
        source_id=args.source,
        registry_config_path=args.registry_config,
        external_config_path=args.external_config,
    )
    snapshot = load_snapshot(args.snapshot)
    async with SupabaseClient(settings) as client:
        provider = DocsReprocessingRuntimeProvider(client, workspace=args.workspace, limit=args.limit)
        inventory = await provider.load_inventory(scope.source_id)
    return build_reconciliation_plan(scope=scope, inventory=inventory, snapshot=snapshot)


def main() -> None:
    """CLI entry point."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
