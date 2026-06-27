"""Run RAG v2 evaluation cases."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.eval.reports import render_markdown_report, write_report_files
from app.eval.runner import run_eval


def main() -> None:
    """Run the evaluation suite."""
    parser = argparse.ArgumentParser(description="Run RAG v2 eval cases")
    parser.add_argument("--cases", type=Path, default=Path("app/eval/cases.json"))
    parser.add_argument("--predictions", type=Path, default=None)
    parser.add_argument("--save-report", action="store_true")
    parser.add_argument("--report-dir", type=Path, default=Path("eval_runs"))
    parser.add_argument("--fail-under", type=float, default=None)
    args = parser.parse_args()

    report = run_eval(cases_path=args.cases, predictions_path=args.predictions)
    data = report.to_dict()
    if args.save_report:
        latest_json, latest_md, timestamp_json, timestamp_md = write_report_files(report, args.report_dir)
        print(f"Wrote {latest_json}")
        print(f"Wrote {latest_md}")
        print(f"Wrote {timestamp_json}")
        print(f"Wrote {timestamp_md}")
    else:
        print(render_markdown_report(data))

    if args.fail_under is not None and data["summary"]["average_final_score"] < args.fail_under:
        print(
            "Average final score {score:.3f} is below threshold {threshold:.3f}".format(
                score=float(data["summary"]["average_final_score"]),
                threshold=args.fail_under,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
