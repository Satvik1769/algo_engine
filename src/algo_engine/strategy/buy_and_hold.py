"""Buy and hold: enter each symbol on its first bar and never exit. A baseline and an engine sanity check."""
from __future__ import annotations

from algo_engine.strategy.base import Action, Bar, HistoryView, PortfolioView, Signal, Strategy, StrategyContext
from algo_engine.strategy.registry import register_strategy


@register_strategy("buy_and_hold")
class BuyAndHold(Strategy):
    def on_start(self, ctx: StrategyContext) -> None:
        self._seen: set[int] = set()

    def on_bar(self, bar: Bar, history: HistoryView, portfolio: PortfolioView) -> list[Signal]:
        if bar.fin_code in self._seen:
            return []
        self._seen.add(bar.fin_code)
        return [Signal(bar.fin_code, bar.trading_date, Action.ENTER_LONG, self.name, "first bar")]
