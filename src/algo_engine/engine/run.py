"""Command line: run a backtest from a YAML config against the local bar store.

    python -m algo_engine.engine.run --config configs/noop_nifty50_index.yaml [--out runs]

Bars come from the `algo` schema (see `python -m algo_engine.data.load`). The source database is not touched.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from algo_engine.data.store import BarStore
from algo_engine.engine.config import RunConfig
from algo_engine.engine.loop import run_backtest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=Path("runs"))
    args = ap.parse_args(argv)

    config = RunConfig.from_yaml(args.config)
    store = BarStore.from_env()
    bars = store.read_bars(list(config.symbols), config.start, config.end)
    dividends = store.read_dividends(list(config.symbols), config.start, config.end)
    result = run_backtest(config, bars, output_dir=args.out, dividends=dividends)
    print(f"run_id={result.run_id} days={result.calendar_days} events={len(result.events)} "
          f"signals={len(result.signals)} trades={len(result.trades)} "
          f"dividends_in_input={len(dividends)}")
    final = result.equity["equity"].iloc[-1]
    print(f"initial_capital={config.initial_capital:,.2f} final_equity={final:,.2f}")
    print(f"data_fingerprint={result.data_fingerprint}")
    print(f"written to {result.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
