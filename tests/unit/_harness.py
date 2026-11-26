"""Stand-in for the M4 engine: feeds bars one at a time with a truncated history view."""
from __future__ import annotations

import pandas as pd

from algo_engine.strategy import Bar, Signal, Strategy, StrategyContext


class TruncatedHistory:
    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame  # already cut at the current bar

    def bars(self, fin_code: int, n: int | None = None) -> pd.DataFrame:
        f = self._frame[self._frame["fin_code"] == fin_code]
        return f.tail(n) if n else f


class FakePortfolio:
    """Long-only, fills each ENTER/EXIT signal immediately at quantity 1 (enough for strategy tests)."""

    def __init__(self) -> None:
        self.qty: dict[int, int] = {}

    def position_qty(self, fin_code: int) -> int:
        return self.qty.get(fin_code, 0)

    def apply(self, signal: Signal) -> None:
        self.qty[signal.fin_code] = 1 if signal.action.value == "enter_long" else 0


def bars_frame(closes: list[float], fin_code: int = 1, start: str = "2024-01-01") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=len(closes)).date
    return pd.DataFrame({
        "fin_code": fin_code, "trading_date": dates, "open": closes,
        "high": [c + 1 for c in closes], "low": [c - 1 for c in closes], "close": closes, "volume": 1000.0,
    })


def run(strategy: Strategy, frame: pd.DataFrame) -> list[Signal]:
    strategy.on_start(StrategyContext(tuple(sorted(frame["fin_code"].unique()))))
    portfolio, out = FakePortfolio(), []
    for i in range(len(frame)):
        row = frame.iloc[i]
        bar = Bar(int(row.fin_code), row.trading_date, row.open, row.high, row.low, row.close, row.volume)
        for sig in strategy.on_bar(bar, TruncatedHistory(frame.iloc[: i + 1]), portfolio):
            portfolio.apply(sig)
            out.append(sig)
    return out
