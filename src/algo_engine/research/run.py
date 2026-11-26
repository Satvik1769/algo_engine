"""Command line: run a parameter experiment against the local bar store.

    python -m algo_engine.research.run --config configs/ema_crossover_nifty50_index.yaml \
        --grid configs/grid_ema.yaml [--test-start 2022-01-01 | --walk-forward 36 12] [--out runs/experiments]

The grid file maps dotted config paths to candidate lists:

    grid:
      strategy.params.fast: [5, 8, 12, 16]
      strategy.params.slow: [20, 26, 40, 60]

Without --test-start or --walk-forward the whole window is searched and the result is labelled in-sample.
Writes summary.json, registry.csv and heatmaps into <out>/<experiment_id>/.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import yaml

from algo_engine.config.settings import ConfigError
from algo_engine.engine.config import RunConfig
from algo_engine.research.experiment import OBJECTIVES, format_summary, run_experiment


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, type=Path, help="base run config (YAML)")
    ap.add_argument("--grid", required=True, type=Path, help="YAML file with a `grid:` mapping")
    ap.add_argument("--test-start", type=date.fromisoformat, help="first day of the out-of-sample window")
    ap.add_argument("--walk-forward", nargs=2, type=int, metavar=("TRAIN_MONTHS", "TEST_MONTHS"))
    ap.add_argument("--objective", default="sharpe", choices=OBJECTIVES)
    ap.add_argument("--rf", type=float, default=0.065)
    ap.add_argument("--mc", type=int, default=2000, help="Monte Carlo resamples")
    ap.add_argument("--out", type=Path, default=Path("runs/experiments"))
    args = ap.parse_args(argv)

    base = RunConfig.from_yaml(args.config)
    raw = yaml.safe_load(args.grid.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "grid" not in raw:
        raise ConfigError(f"{args.grid} must contain a top-level `grid:` mapping")

    from algo_engine.data.store import BarStore
    store = BarStore.from_env()
    bars = store.read_bars(list(base.symbols), base.start, base.end)
    dividends = store.read_dividends(list(base.symbols), base.start, base.end)
    summary = run_experiment(base, raw["grid"], bars, dividends, test_start=args.test_start,
                             walk=tuple(args.walk_forward) if args.walk_forward else None, rf=args.rf,
                             objective=args.objective, n_mc=args.mc, out_dir=args.out)
    print(format_summary(summary))
    print(f"written to {summary['output_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
