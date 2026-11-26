"""EMA crossover (long-only): enter when the fast EMA crosses above the slow EMA, exit on the cross below.

Signals fire on the bar where the cross is first visible at the close (fast_prev <= slow_prev and
fast > slow, and the mirror). They fill from the next bar. No signal before both EMAs are warmed up
(slow bars of history plus one more bar to see a cross). A state that is already fast > slow at the
first valid bar is not a cross, so it produces no signal. Indicators run incrementally per symbol, so
the result cannot depend on future bars.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isnan

from algo_engine.indicators import IncrementalEma
from algo_engine.strategy.base import (
    Action, Bar, HistoryView, ParamError, PortfolioView, Signal, Strategy, StrategyContext, StrategyParams,
)
from algo_engine.strategy.registry import register_strategy


@dataclass(frozen=True)
class EmaCrossoverParams(StrategyParams):
    fast: int = 12
    slow: int = 26

    def __post_init__(self) -> None:
        for key in ("fast", "slow"):
            value = getattr(self, key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 2:
                raise ParamError(f"{key} must be an integer >= 2, got {value!r}")
        if self.fast >= self.slow:
            raise ParamError(f"fast ({self.fast}) must be smaller than slow ({self.slow})")


@dataclass
class _SymbolState:
    fast: IncrementalEma
    slow: IncrementalEma
    prev_diff: float = float("nan")


@register_strategy("ema_crossover")
class EmaCrossover(Strategy):
    params_cls = EmaCrossoverParams
    params: EmaCrossoverParams

    def __init__(self, params: StrategyParams) -> None:
        super().__init__(params)
        self._state: dict[int, _SymbolState] = {}

    def on_start(self, ctx: StrategyContext) -> None:
        self._state = {}

    def on_bar(self, bar: Bar, history: HistoryView, portfolio: PortfolioView) -> list[Signal]:
        st = self._state.setdefault(
            bar.fin_code,
            _SymbolState(IncrementalEma(self.params.fast), IncrementalEma(self.params.slow)),
        )
        fast, slow = st.fast.update(bar.close), st.slow.update(bar.close)
        diff = fast - slow  # NaN until the slow EMA is warmed up
        prev, st.prev_diff = st.prev_diff, diff
        if isnan(diff) or isnan(prev):
            return []
        held = portfolio.position_qty(bar.fin_code) > 0
        if prev <= 0 < diff and not held:
            return [Signal(bar.fin_code, bar.trading_date, Action.ENTER_LONG, self.name,
                           f"EMA{self.params.fast} crossed above EMA{self.params.slow}")]
        if prev >= 0 > diff and held:
            return [Signal(bar.fin_code, bar.trading_date, Action.EXIT_LONG, self.name,
                           f"EMA{self.params.fast} crossed below EMA{self.params.slow}")]
        return []
