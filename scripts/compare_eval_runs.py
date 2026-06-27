"""Compare two RAG v2 evaluation reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.eval.reports import compare_eval_reports, load_report, render_compare_markdown


def main() -> None:
    """Compare a baseline and current eval run."""
    parser = argparse.ArgumentParser(description="Compare two RAG v2 eval report JSON files")
    parser.add_argument("baseline", type=Path)
    parser.add_argument("current", type=Path)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of Markdown")
    args = parser.parse_args()

    comparison = compare_eval_reports(load_report(args.baseline), load_report(args.current))
    if args.json:
        print(json.dumps(comparison, ensure_ascii=False, indent=2))
    else:
        print(render_compare_markdown(comparison))

    if comparison["summary"]["regression_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
