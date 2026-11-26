"""Command line: compute metrics for a finished run and write `metrics.json` into its directory.

    python -m algo_engine.analytics.run --run-dir runs/<run_id> [--rf 0.065] [--benchmark 100256265 | --no-benchmark]

The benchmark (default: the Nifty 50 price index) is read from the local bar store.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from algo_engine.analytics.report import (
    BENCHMARK_LABEL, DEFAULT_RISK_FREE, NIFTY50_INDEX_FIN_CODE, analyze, benchmark_series, format_summary, load_run,
    write_benchmark, write_metrics,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--rf", type=float, default=DEFAULT_RISK_FREE, help="annual risk-free rate (default 0.065)")
    ap.add_argument("--benchmark", type=int, default=NIFTY50_INDEX_FIN_CODE, help="benchmark fin_code in the store")
    ap.add_argument("--no-benchmark", action="store_true")
    ap.add_argument("--report", action="store_true", help="also build report.html in the run directory")
    ap.add_argument("--bootstrap", type=int, default=2000, help="bootstrap resamples")
    args = ap.parse_args(argv)

    run = load_run(args.run_dir)
    bench = None
    if not args.no_benchmark:
        from algo_engine.data.store import BarStore
        bench = benchmark_series(BarStore.from_env(), args.benchmark, run.equity["trading_date"].iloc[0],
                                 run.equity["trading_date"].iloc[-1])
    label = BENCHMARK_LABEL if args.benchmark == NIFTY50_INDEX_FIN_CODE else f"benchmark {args.benchmark}"
    metrics = analyze(run, bench, args.rf, args.bootstrap, label)
    path = write_metrics(args.run_dir, metrics)
    if bench is not None:
        write_benchmark(args.run_dir, bench, list(run.equity["trading_date"]))
    print(format_summary(metrics))
    print(f"written to {path}")
    if args.report:
        from algo_engine.reports import build_report
        print(f"written to {build_report(args.run_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
