"""Truncated history: the only market data a strategy can reach (CLAUDE.md section 6.1)."""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from datetime import date

import pandas as pd

from algo_engine.engine.errors import EngineError


class SymbolSeries:
    """All bars of one symbol, sorted by date. Held by the engine, never handed to a strategy."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame.reset_index(drop=True)
        self.dates: list[date] = list(self.frame["trading_date"])


class TruncatedHistory:
    """Read-only view with every bar dated after `as_of` hidden. Built by the engine for each bar."""

    def __init__(self, series: Mapping[int, SymbolSeries], as_of: date) -> None:
        self._series = series
        self._as_of = as_of

    def bars(self, fin_code: int, n: int | None = None) -> pd.DataFrame:
        """Up to the last `n` bars (all if None) with trading_date <= as_of, oldest first. Returns a copy."""
        try:
            s = self._series[fin_code]
        except KeyError:
            raise EngineError(f"History requested for symbol {fin_code}, which is not in the run") from None
        if n is not None and n < 0:
            raise ValueError(f"n must be >= 0, got {n}")
        end = bisect_right(s.dates, self._as_of)
        begin = 0 if n is None else max(0, end - n)
        return s.frame.iloc[begin:end].copy()
