"""Slippage: the fill price moves against the trader by a fixed number of basis points."""
from __future__ import annotations

from algo_engine.engine.events import Side


class FixedBpsSlippage:
    """BUY pays price * (1 + bps/10000), SELL receives price * (1 - bps/10000)."""

    def __init__(self, bps: float) -> None:
        if bps < 0:
            raise ValueError(f"slippage bps must be >= 0, got {bps}")
        self.bps = bps

    def adjust(self, side: Side, price: float) -> float:
        factor = self.bps / 10_000.0
        return price * (1.0 + factor) if side is Side.BUY else price * (1.0 - factor)
