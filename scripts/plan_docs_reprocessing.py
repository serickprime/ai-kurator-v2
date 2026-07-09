"""Read-only source-scoped docs reprocessing preflight and baseline tooling."""

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
from app.docs_registry.reprocessing_plan import (  # noqa: E402
    DEFAULT_WORKSPACE,
    DocsReprocessingPlanError,
    DocsReprocessingRuntimeProvider,
    build_baseline_manifest,
    build_reprocessing_plan,
    compare_manifest_to_plan,
    format_plan_text,
    format_verification_text,
    load_manifest,
    resolve_source_scope,
    validate_execution_preconditions,
    verify_manifest,
    write_manifest_atomic,
)
from app.external_docs.config import DEFAULT_EXTERNAL_DOCS_CONFIG  # noqa: E402
from app.service_registry.config import DEFAULT_SERVICE_REGISTRY_CONFIG  # noqa: E402
from app.service_registry.docs_health import (  # noqa: E402
    DEFAULT_DOCS_HEALTH_POLICY_CONFIG,
    DocsHealthReportProvider,
)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Prepare read-only source-scoped docs reprocessing plans and manifests.",
    )
    parser.add_argument("--service", help="Canonical service id or alias, for example openrouter.")
    parser.add_argument("--source", help="Optional explicit source_id safety check.")
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE, help="Workspace name to inspect.")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Output format.")
    parser.add_argument("--limit", type=int, default=10000, help="Max rows per read-only table query.")
    parser.add_argument("--export", type=Path, help="Write a local source-scoped baseline manifest.")
    parser.add_argument("--force", action="store_true", help="Allow overwriting an existing export path.")
    parser.add_argument("--verify", type=Path, help="Verify an existing manifest without Supabase writes.")
    parser.add_argument("--compare-live", action="store_true", help="Compare verified manifest with live inventory.")
    parser.add_argument("--registry-config", type=Path, default=DEFAULT_SERVICE_REGISTRY_CONFIG)
    parser.add_argument("--external-config", type=Path, default=DEFAULT_EXTERNAL_DOCS_CONFIG)
    parser.add_argument("--policy-config", type=Path, default=DEFAULT_DOCS_HEALTH_POLICY_CONFIG)
    return parser.parse_args()


async def main_async() -> int:
    """Run read-only planning, export, or verification."""
    args = parse_args()
    try:
        if args.verify:
            return await _run_verify(args)
        if not args.service:
            raise DocsReprocessingPlanError("--service is required unless --verify is used")
        plan, inventory = await _build_live_plan(args, full_export=bool(args.export))
        if args.export:
            manifest = build_baseline_manifest(plan=plan, inventory=inventory, include_rows=True)
            write_manifest_atomic(manifest, args.export, force=args.force)
            if args.format == "json":
                print(json.dumps({"exported": str(args.export), "plan": plan.to_dict()}, ensure_ascii=False, indent=2))
            else:
                print(format_plan_text(plan))
                print("")
                print(f"Baseline manifest exported: {args.export}")
                print("Supabase writes: disabled")
                print("Activation/reprocessing: not performed")
            return 0
        if args.format == "json":
            print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(format_plan_text(plan))
        return 0
    except DocsReprocessingPlanError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI must not expose runtime tracebacks
        print(f"ERROR: runtime unavailable: {exc.__class__.__name__}", file=sys.stderr)
        return 2


async def _run_verify(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.verify)
    verification = verify_manifest(
        manifest,
        expected_service=args.service,
        expected_source=args.source,
    )
    drift = None
    preconditions = None
    if args.compare_live:
        service = args.service or str(manifest.get("service_id") or "")
        source = args.source or str(manifest.get("source_id") or "")
        if not service:
            raise DocsReprocessingPlanError("--service is required for --compare-live when manifest has no service_id")
        plan, _inventory = await _build_live_plan(args, service=service, source=source, full_export=False)
        drift = compare_manifest_to_plan(manifest, plan)
        preconditions = validate_execution_preconditions(
            manifest_result=verification,
            drift_result=drift,
            current_plan=plan,
            runtime_available=True,
        )
    if args.format == "json":
        payload: dict[str, object] = {"verification": verification.to_dict()}
        if drift is not None:
            payload["drift"] = drift.to_dict()
        if preconditions is not None:
            payload["preconditions"] = preconditions.to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_verification_text(verification, drift=drift, preconditions=preconditions))
    return 0 if verification.valid and (drift is None or drift.matches) else 2


async def _build_live_plan(
    args: argparse.Namespace,
    *,
    service: str | None = None,
    source: str | None = None,
    full_export: bool,
) -> tuple[object, object]:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise DocsReprocessingPlanError("runtime unavailable: Supabase settings are not configured")

    scope = resolve_source_scope(
        service or args.service,
        source_id=source or args.source,
        registry_config_path=args.registry_config,
        external_config_path=args.external_config,
    )
    async with SupabaseClient(settings) as client:
        provider = DocsReprocessingRuntimeProvider(client, workspace=args.workspace, limit=args.limit)
        inventory = (
            await provider.load_full_export_rows(scope.source_id)
            if full_export
            else await provider.load_inventory(scope.source_id)
        )
        health_provider = DocsHealthReportProvider(
            client,
            registry_config_path=args.registry_config,
            external_config_path=args.external_config,
            policy_config_path=args.policy_config,
            limit=args.limit,
        )
        health_report = await health_provider.build_report()
    plan = build_reprocessing_plan(scope=scope, inventory=inventory, health_report=health_report)
    return plan, inventory


def main() -> None:
    """CLI entry point."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
