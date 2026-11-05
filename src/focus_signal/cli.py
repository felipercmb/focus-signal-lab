"""Small offline-first command line interface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from focus_signal.data import download_snapshot, load_snapshot
from focus_signal.evaluation import run_experiment
from focus_signal.features import build_features


def main() -> None:
    parser = argparse.ArgumentParser(description="Reproduce the Focus Signal Lab benchmark")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-data", help="Verify checksums and series contracts offline")
    check.add_argument("--data", type=Path, default=Path("data/snapshot"))
    run = commands.add_parser("run", help="Run temporal evaluation using a local data snapshot")
    run.add_argument("--data", type=Path, default=Path("data/snapshot"))
    run.add_argument("--output", type=Path, default=Path("reports/local"))
    run.add_argument(
        "--lag-days", type=int, default=7, help="Availability lag; at least 7 calendar days"
    )
    fetch = commands.add_parser("download", help="Download public BCB data into a NEW directory")
    fetch.add_argument("--output", type=Path, required=True)
    fetch.add_argument("--start", default="2010-01-01")
    fetch.add_argument("--end", default="2025-12-31")
    args = parser.parse_args()
    if args.command == "download":
        manifest = download_snapshot(args.output, args.start, args.end)
        print(json.dumps(manifest["series"], indent=2))
    else:
        focus, selic, fx = load_snapshot(args.data)
        if args.command == "check-data":
            print(f"Verified snapshot: Focus={len(focus)}, Selic={len(selic)}, FX={len(fx)} rows")
        else:
            from focus_signal.reporting import write_report

            frame = build_features(focus, selic, fx, lag_days=args.lag_days)
            result = run_experiment(frame)
            write_report(result, frame, args.data, args.output)
            print(result.metrics.to_string(index=False))
            print(f"Selected on validation: {result.selection['selected_model']}")
            print(f"Artifacts saved to {args.output}")


if __name__ == "__main__":
    main()
