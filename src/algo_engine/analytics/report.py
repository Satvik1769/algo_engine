"""Analyze a finished run directory: metrics, benchmark comparison, bootstrap intervals, metrics.json.

Inputs are the files `engine.output.write_run` produced (config.json, equity.csv, trades.csv, events.jsonl,
manifest.json) plus an optional benchmark close series. Nothing here changes a run; the output has no wall-clock
values, so the same inputs give identical bytes.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from algo_engine.analytics.metrics import (
    LOW_SAMPLE_TRADES, TRADING_DAYS, bootstrap_ci, daily_returns, equity_metrics, exposure_metrics,
    relative_metrics, sharpe_ratio, trade_metrics, trade_returns,
)

NIFTY50_INDEX_FIN_CODE = 100256265
BENCHMARK_LABEL = "Nifty 50 (price return)"
DEFAULT_RISK_FREE = 0.065


@dataclass(frozen=True)
class RunData:
    run_dir: Path
    config: dict
    manifest: dict
    equity: pd.DataFrame     # trading_date (date), cash, positions_value, equity
    trades: pd.DataFrame
    fills: int
    total_costs: float
    total_dividends: float


def load_run(run_dir: str | Path) -> RunData:
    d = Path(run_dir)
    equity = pd.read_csv(d / "equity.csv")
    equity["trading_date"] = pd.to_datetime(equity["trading_date"]).dt.date
    trades = pd.read_csv(d / "trades.csv")
    fills, costs, dividends = 0, 0.0, 0.0
    with (d / "events.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            if '"FillEvent"' in line:
                fills += 1
                costs += json.loads(line)["costs"]
            elif '"DividendEvent"' in line:
                dividends += json.loads(line)["cash"]
    return RunData(d, json.loads((d / "config.json").read_text()), json.loads((d / "manifest.json").read_text()),
                   equity, trades, fills, costs, dividends)


def benchmark_series(store, fin_code: int, start: date, end: date) -> pd.Series:
    """Close of the benchmark index per trading date from the bar store."""
    bars = store.read_bars([fin_code], start, end)
    if bars.empty:
        raise ValueError(f"No stored bars for benchmark {fin_code} between {start} and {end}")
    return pd.Series(bars["close"].to_numpy(dtype=float), index=list(bars["trading_date"]), name="benchmark")


def analyze(run: RunData, benchmark: pd.Series | None = None, rf: float = DEFAULT_RISK_FREE,
            n_boot: int = 2000, benchmark_label: str = BENCHMARK_LABEL) -> dict:
    equity = pd.Series(run.equity["equity"].to_numpy(dtype=float), index=list(run.equity["trading_date"]))
    seed = int(run.config.get("seed", 0))
    returns = daily_returns(equity).to_numpy()
    strategy = equity_metrics(equity, rf)
    strategy.update(exposure_metrics(run.equity))
    trades = trade_metrics(run.trades, list(run.equity["trading_date"]))
    trades["total_fills"] = run.fills
    out: dict = {
        "meta": {"run_id": run.manifest["run_id"], "risk_free_rate": rf, "periods_per_year": TRADING_DAYS,
                 "initial_capital": run.config.get("initial_capital"), "strategy": run.config.get("strategy"),
                 "symbols": run.config.get("symbols"), "data_fingerprint": run.manifest.get("data_fingerprint"),
                 "bootstrap_resamples": n_boot, "bootstrap_seed": seed,
                 "bootstrap_method": "percentile, i.i.d. resample"},
        "strategy": strategy,
        "trades": trades,
        "costs": {"total_costs": run.total_costs, "total_dividends": run.total_dividends,
                  "costs_pct_of_initial_capital": run.total_costs / run.config["initial_capital"]
                  if run.config.get("initial_capital") else math.nan},
    }
    confidence: dict = {
        "sharpe": bootstrap_ci(returns, lambda s: sharpe_ratio(s, rf, axis=1), n_boot, seed),
    }
    if len(run.trades):
        tr = trade_returns(run.trades)
        confidence["mean_trade_return"] = bootstrap_ci(tr, lambda s: s.mean(axis=1), n_boot, seed)
        confidence["win_rate"] = bootstrap_ci((run.trades["net_pnl"].to_numpy() > 0).astype(float),
                                              lambda s: s.mean(axis=1), n_boot, seed)
    out["confidence"] = confidence

    warnings = []
    if len(run.trades) < LOW_SAMPLE_TRADES:
        warnings.append(f"Only {len(run.trades)} closed trades (fewer than {LOW_SAMPLE_TRADES}): "
                        "trade statistics and their intervals are weak evidence.")
    if len(equity) < TRADING_DAYS:
        warnings.append(f"History is {len(equity)} trading days, shorter than one year; annualized figures are unstable.")
    if int(strategy["observations"]) >= 2 and math.isnan(strategy["sharpe"]):
        warnings.append("Sharpe is undefined (returns have no variance).")
    warnings.append("Sharpe interval assumes independent daily returns; it ignores autocorrelation.")
    warnings.append("Open positions at the end of the run are valued at the last close and are not in the trade statistics.")

    if benchmark is not None:
        bench = benchmark.sort_index()
        common = equity.index.intersection(bench.index)
        out["benchmark"] = {"label": benchmark_label, **equity_metrics(bench.loc[common], rf)}
        out["relative"] = relative_metrics(equity, bench, rf)
        if len(common) < len(equity):
            warnings.append(f"Benchmark has {len(common)} of {len(equity)} run dates; comparison uses the common dates.")
        if "price return" in benchmark_label:
            warnings.append("Benchmark is a price index without dividends, so it understates a total-return benchmark.")
    out["warnings"] = warnings
    return _clean(out)


def _clean(value):
    """JSON-safe: NaN -> None, +/-inf -> "inf"/"-inf", dates -> ISO strings, numpy scalars -> Python."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
    return value


def write_benchmark(run_dir: str | Path, benchmark: pd.Series, run_dates: list[date]) -> Path:
    """Save the benchmark closes on the run's dates, so the report can be rebuilt from the run directory alone."""
    common = benchmark.sort_index().loc[benchmark.index.intersection(run_dates)]
    frame = pd.DataFrame({"trading_date": [d.isoformat() for d in common.index], "close": common.to_numpy()})
    path = Path(run_dir) / "benchmark.csv"
    frame.to_csv(path, index=False, lineterminator="\n")
    return path


def write_metrics(run_dir: str | Path, metrics: dict) -> Path:
    path = Path(run_dir) / "metrics.json"
    path.write_text(json.dumps(metrics, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def format_summary(m: dict) -> str:
    s, t = m["strategy"], m["trades"]

    def pct(x):
        return "n/a" if x is None else f"{x * 100:.2f}%"

    def num(x):
        return "n/a" if x is None else (x if isinstance(x, str) else f"{x:.2f}")

    lines = [f"run {m['meta']['run_id']}  rf={m['meta']['risk_free_rate']:.1%}",
             f"  total return {pct(s['total_return'])}  CAGR {pct(s['cagr'])}  vol {pct(s['annualized_volatility'])}",
             f"  Sharpe {num(s['sharpe'])}  Sortino {num(s['sortino'])}  Calmar {num(s['calmar'])}",
             f"  max drawdown {pct(s['max_drawdown'])}  longest underwater {s['longest_underwater_trading_days']} trading days",
             f"  exposure {pct(s['mean_exposure'])}  days invested {pct(s['pct_days_invested'])}",
             f"  trades {t['closed_trades']}  win rate {pct(t['win_rate'])}  profit factor {num(t['profit_factor'])}  "
             f"costs {m['costs']['total_costs']:,.0f}  dividends {m['costs']['total_dividends']:,.0f}"]
    ci = m["confidence"].get("sharpe")
    if ci:
        lines.append(f"  Sharpe {ci['level']:.0%} interval [{ci['low']:.2f}, {ci['high']:.2f}]")
    if "benchmark" in m:
        b, r = m["benchmark"], m["relative"]
        lines += [f"  {b['label']}: total return {pct(b['total_return'])}  CAGR {pct(b['cagr'])}  Sharpe {num(b['sharpe'])}  "
                  f"max drawdown {pct(b['max_drawdown'])}",
                  f"  vs benchmark: excess return {pct(r['excess_total_return'])}  beta {num(r['beta'])}  "
                  f"alpha {pct(r['alpha_annualized'])}  tracking error {pct(r['tracking_error'])}  "
                  f"information ratio {num(r['information_ratio'])}"]
    lines += [f"  ! {w}" for w in m["warnings"]]
    return "\n".join(lines)
