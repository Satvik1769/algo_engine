"""Command line: build report.html for a run directory that already has metrics.json.

    python -m algo_engine.reports.build --run-dir runs/<run_id>
"""
from __future__ import annotations

import argparse
from pathlib import Path

from algo_engine.reports.static import build_report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True, type=Path)
    args = ap.parse_args(argv)
    print(f"written to {build_report(args.run_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
