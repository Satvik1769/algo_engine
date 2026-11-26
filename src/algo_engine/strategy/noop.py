"""A strategy that never trades. Used to prove the engine loop on real data (M4 acceptance)."""
from __future__ import annotations

from algo_engine.strategy.base import Bar, HistoryView, PortfolioView, Signal, Strategy
from algo_engine.strategy.registry import register_strategy


@register_strategy("noop")
class NoOp(Strategy):
    def on_bar(self, bar: Bar, history: HistoryView, portfolio: PortfolioView) -> list[Signal]:
        return []
