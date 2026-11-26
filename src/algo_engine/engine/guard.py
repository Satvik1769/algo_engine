"""Look-ahead guard (CLAUDE.md section 9): signals at `t` must not depend on data after `t`.

For each cut date `c`, the run is repeated with the data and the config end cut at `c`. The signals formed
on `c` must equal those of the full run. A strategy that reaches data after `c` through anything the engine
supplies (the history view's internals, the bars it was given) produces different signals and is reported.
Limit: a strategy that reads a source outside the engine input (a file, a global holding the full table) sees
the same data in both runs, so this guard cannot detect it. Strategy code review covers that case.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import pandas as pd

from algo_engine.engine.config import RunConfig
from algo_engine.engine.errors import LookaheadError
from algo_engine.engine.loop import prepare_bars, run_backtest
from algo_engine.strategy import Signal


def _on(signals: list[Signal], day: date) -> list[tuple]:
    return [(s.fin_code, s.action.value, s.strategy_id, s.reason) for s in signals if s.trading_date == day]


def find_lookahead(config: RunConfig, bars: pd.DataFrame, step: int = 1) -> list[date]:
    """Cut dates (every `step`-th calendar date) where truncated and full runs disagree. Empty means causal."""
    if step < 1:
        raise ValueError("step must be >= 1")
    frame = prepare_bars(config, bars)
    full = run_backtest(config, frame)
    calendar = sorted(set(frame["trading_date"]))
    bad: list[date] = []
    for cut in calendar[::step]:
        part = run_backtest(replace(config, end=cut), frame[frame["trading_date"] <= cut])
        if _on(part.signals, cut) != _on(full.signals, cut):
            bad.append(cut)
    return bad


def assert_no_lookahead(config: RunConfig, bars: pd.DataFrame, step: int = 1) -> None:
    bad = find_lookahead(config, bars, step)
    if bad:
        raise LookaheadError(f"Signals depend on future data; first differing dates: {bad[:5]}")
