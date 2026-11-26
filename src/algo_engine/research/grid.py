"""Parameter grid expansion and single-trial evaluation.

A grid maps dotted config paths to candidate values, e.g. `{"strategy.params.fast": [5, 8], "slippage_bps": [5, 10]}`.
Every combination becomes a RunConfig (base config + overrides + window). Combinations the config rejects (for
example fast >= slow) are reported as invalid and are not trials.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from algo_engine.analytics.metrics import TRADING_DAYS, daily_returns, equity_metrics, trade_returns
from algo_engine.config.settings import ConfigError
from algo_engine.engine.config import RunConfig
from algo_engine.engine.loop import run_backtest
from algo_engine.strategy.base import ParamError

INVALID_ERRORS = (ConfigError, ParamError)


def expand_grid(grid: dict[str, list]) -> list[dict[str, Any]]:
    """Cartesian product of the candidate values, in the order the keys and values are given."""
    if not isinstance(grid, dict) or not grid:
        raise ConfigError("grid must be a non-empty mapping of config path -> list of values")
    for path, values in grid.items():
        if not isinstance(path, str) or not path:
            raise ConfigError(f"grid keys must be dotted config paths, got {path!r}")
        if not isinstance(values, (list, tuple)) or not values:
            raise ConfigError(f"grid values for {path!r} must be a non-empty list")
    keys = list(grid)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(grid[k] for k in keys))]


def set_path(raw: dict, path: str, value: Any) -> None:
    keys = path.split(".")
    cur = raw
    for key in keys[:-1]:
        nxt = cur.setdefault(key, {})
        if not isinstance(nxt, dict):
            raise ConfigError(f"Cannot set {path!r}: {key!r} is not a mapping")
        cur = nxt
    cur[keys[-1]] = value


def make_config(base: RunConfig, overrides: dict[str, Any], start: date | None = None,
                end: date | None = None) -> RunConfig:
    """Base config with dotted-path overrides and an optional window. Raises ConfigError/ParamError if invalid."""
    raw = base.to_dict()
    if start is not None:
        raw["start"] = start
    if end is not None:
        raw["end"] = end
    for path, value in overrides.items():
        set_path(raw, path, value)
    return RunConfig.from_dict(raw)


@dataclass(frozen=True)
class Trial:
    overrides: dict[str, Any]
    config: RunConfig
    run_id: str
    metrics: dict[str, float]
    returns: np.ndarray          # daily equity returns
    net_pnl: np.ndarray          # per closed trade, in rupees
    trade_returns: np.ndarray    # per closed trade, fraction of value bought


def period_sharpe(returns: np.ndarray, rf: float = 0.0, periods: int = TRADING_DAYS) -> float:
    """Non-annualized Sharpe of daily returns (NaN when undefined)."""
    r = np.asarray(returns, dtype=float)
    if len(r) < 2:
        return float("nan")
    excess = r - rf / periods
    sd = excess.std(ddof=1)
    return float(excess.mean() / sd) if sd > 1e-12 else float("nan")


def run_trial(config: RunConfig, bars: pd.DataFrame, dividends: pd.DataFrame | None, overrides: dict[str, Any],
              rf: float) -> Trial:
    result = run_backtest(config, bars, dividends=dividends)
    equity = pd.Series(result.equity["equity"].to_numpy(dtype=float), index=list(result.equity["trading_date"]))
    em = equity_metrics(equity, rf)
    returns = daily_returns(equity).to_numpy()
    trades = pd.DataFrame([vars(t) for t in result.trades])
    pnl = trades["net_pnl"].to_numpy(dtype=float) if len(trades) else np.array([])
    tr = trade_returns(trades) if len(trades) else np.array([])
    metrics = {"sharpe": em["sharpe"], "cagr": em["cagr"], "total_return": em["total_return"],
               "max_drawdown": em["max_drawdown"], "calmar": em["calmar"], "trades": float(len(trades)),
               "final_equity": float(equity.iloc[-1]), "days": float(len(equity))}
    return Trial(overrides, config, result.run_id, metrics, returns, pnl, tr)


def run_grid(base: RunConfig, grid: dict[str, list], bars: pd.DataFrame, dividends: pd.DataFrame | None,
             start: date, end: date, rf: float) -> tuple[list[Trial], list[dict[str, Any]]]:
    """Run every valid combination on [start, end]. Returns (trials, invalid combinations with the reason)."""
    trials, invalid = [], []
    for overrides in expand_grid(grid):
        try:
            config = make_config(base, overrides, start, end)
        except INVALID_ERRORS as exc:
            invalid.append({"overrides": overrides, "reason": str(exc)})
            continue
        trials.append(run_trial(config, bars, dividends, overrides, rf))
    return trials, invalid


def select_best(trials: list[Trial], objective: str = "sharpe") -> int:
    """Index of the trial with the highest objective; NaN counts as worst; ties go to the first in grid order."""
    if not trials:
        raise ValueError("no trials to select from")
    scores = [t.metrics.get(objective, float("nan")) for t in trials]
    keyed = [(-np.inf if (s is None or np.isnan(s)) else s) for s in scores]
    return int(np.argmax(keyed))
