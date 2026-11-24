"""Interfaces the loop needs from order management, risk, execution and the portfolio, plus no-op stubs.

M6 (orders, risk, execution) and M7 (portfolio) replace the stubs. `Order` and `Fill` hold only what the
loop needs to enforce the lifecycle; M6 may extend them (order type, stop price, time in force).
The stubs place no orders and fill nothing, so an M4 run proves the loop, not any trading result.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from algo_engine.engine.events import Side
from algo_engine.strategy import Bar, PortfolioView, Signal


@dataclass(frozen=True)
class Order:
    """A DAY order created at the close of `created_on`; eligible to fill from the next bar."""
    order_id: int
    fin_code: int
    side: Side
    quantity: int
    created_on: date
    strategy_id: str


@dataclass(frozen=True)
class Fill:
    order_id: int
    fin_code: int
    side: Side
    quantity: int
    price: float
    trading_date: date
    costs: float = 0.0


@dataclass(frozen=True)
class RiskDecision:
    accepted: bool
    reason: str = ""


@dataclass(frozen=True)
class EquityPoint:
    trading_date: date
    cash: float
    positions_value: float
    equity: float


class OrderManager(Protocol):
    def create_orders(self, signals: Sequence[Signal], portfolio: PortfolioView, trading_date: date) -> list[Order]:
        """Turn the signals formed at the close of `trading_date` into orders (sizing policy lives here)."""
        ...


class RiskManager(Protocol):
    def validate(self, order: Order, portfolio: PortfolioView, bar: Bar) -> RiskDecision: ...


class ExecutionSimulator(Protocol):
    def execute(self, orders: Sequence[Order], bars: Mapping[int, Bar], trading_date: date) -> list[Fill]:
        """Try to fill `orders` using the bars of `trading_date` (symbols without a bar are absent from `bars`)."""
        ...


class Portfolio(PortfolioView, Protocol):
    def apply_fill(self, fill: Fill) -> None: ...

    def mark_to_market(self, trading_date: date, closes: Mapping[int, float]) -> EquityPoint:
        """Value the portfolio at the given closing prices."""
        ...


class NoOpOrderManager:
    def create_orders(self, signals: Sequence[Signal], portfolio: PortfolioView, trading_date: date) -> list[Order]:
        return []


class AcceptAllRisk:
    def validate(self, order: Order, portfolio: PortfolioView, bar: Bar) -> RiskDecision:
        return RiskDecision(True)


class NoFillExecution:
    def execute(self, orders: Sequence[Order], bars: Mapping[int, Bar], trading_date: date) -> list[Fill]:
        return []


class CashOnlyPortfolio:
    """Holds the initial cash and no positions. Real accounting arrives in M7."""

    def __init__(self, initial_cash: float) -> None:
        self._cash = initial_cash

    def position_qty(self, fin_code: int) -> int:
        return 0

    def apply_fill(self, fill: Fill) -> None:
        raise NotImplementedError("CashOnlyPortfolio cannot apply fills; use the M7 portfolio")

    def mark_to_market(self, trading_date: date, closes: Mapping[int, float]) -> EquityPoint:
        return EquityPoint(trading_date, self._cash, 0.0, self._cash)
