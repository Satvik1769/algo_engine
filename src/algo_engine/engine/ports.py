"""Interfaces the loop needs from order management, risk, execution and the portfolio, plus no-op stubs.

M6 provides the order manager, risk manager and execution simulator (`orders/`, `risk/`, `execution/`).
M7 provides the real portfolio (`portfolio/`). `run_backtest` uses them by default (`build_components` in
`engine/build.py`). The no-op stubs below remain for tests and for runs that should not trade;
`CashOnlyPortfolio` holds the initial cash and cannot apply fills.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from algo_engine.engine.errors import ExecutionError
from algo_engine.engine.events import OrderType, Side
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
    order_type: OrderType = OrderType.MARKET
    stop_price: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool) or self.quantity <= 0:
            raise ExecutionError(f"Order {self.order_id}: quantity must be a positive integer, got {self.quantity!r}")
        if self.order_type is OrderType.STOP:
            if self.stop_price is None or not self.stop_price > 0:
                raise ExecutionError(f"Order {self.order_id}: a STOP order needs a positive stop_price")
        elif self.stop_price is not None:
            raise ExecutionError(f"Order {self.order_id}: a MARKET order must not carry a stop_price")


@dataclass(frozen=True)
class Fill:
    order_id: int
    fin_code: int
    side: Side
    quantity: int
    price: float
    trading_date: date
    costs: float = 0.0
    cost_breakdown: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskDecision:
    accepted: bool
    reason: str = ""


@dataclass(frozen=True)
class SkippedSignal:
    signal: Signal
    reason: str


@dataclass(frozen=True)
class SizingResult:
    orders: list[Order] = field(default_factory=list)
    skipped: list[SkippedSignal] = field(default_factory=list)


@dataclass(frozen=True)
class ExecutionReport:
    """Fills, plus orders the executor refused (order_id -> reason). Orders in neither expire."""
    fills: list[Fill] = field(default_factory=list)
    rejected: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EquityPoint:
    trading_date: date
    cash: float
    positions_value: float
    equity: float


class AccountView(PortfolioView, Protocol):
    """Read-only account state for sizing, risk and the cash check at fill time."""

    def cash(self) -> float: ...

    def equity(self) -> float:
        """Equity at the last mark-to-market (the close of the current bar when signals are formed)."""
        ...

    def open_positions(self) -> int: ...


class OrderManager(Protocol):
    def create_orders(self, signals: Sequence[Signal], account: AccountView, bars: Mapping[int, Bar],
                      trading_date: date) -> SizingResult:
        """Turn the signals formed at the close of `trading_date` into orders (sizing policy lives here)."""
        ...


class RiskManager(Protocol):
    def validate(self, order: Order, account: AccountView, bar: Bar) -> RiskDecision: ...


class ExecutionSimulator(Protocol):
    def execute(self, orders: Sequence[Order], bars: Mapping[int, Bar], trading_date: date,
                account: AccountView) -> ExecutionReport:
        """Try to fill `orders` using the bars of `trading_date` (symbols without a bar are absent from `bars`)."""
        ...


class Portfolio(AccountView, Protocol):
    def apply_fill(self, fill: Fill) -> None: ...

    def mark_to_market(self, trading_date: date, closes: Mapping[int, float]) -> EquityPoint:
        """Value the portfolio at the given closing prices."""
        ...


class NoOpOrderManager:
    def create_orders(self, signals: Sequence[Signal], account: AccountView, bars: Mapping[int, Bar],
                      trading_date: date) -> SizingResult:
        return SizingResult()


class AcceptAllRisk:
    def validate(self, order: Order, account: AccountView, bar: Bar) -> RiskDecision:
        return RiskDecision(True)


class NoFillExecution:
    def execute(self, orders: Sequence[Order], bars: Mapping[int, Bar], trading_date: date,
                account: AccountView) -> ExecutionReport:
        return ExecutionReport()


class CashOnlyPortfolio:
    """Holds the initial cash and no positions. Real accounting arrives in M7."""

    def __init__(self, initial_cash: float) -> None:
        self._cash = initial_cash

    def position_qty(self, fin_code: int) -> int:
        return 0

    def cash(self) -> float:
        return self._cash

    def equity(self) -> float:
        return self._cash

    def open_positions(self) -> int:
        return 0

    def apply_fill(self, fill: Fill) -> None:
        raise NotImplementedError("CashOnlyPortfolio cannot apply fills; use the M7 portfolio")

    def mark_to_market(self, trading_date: date, closes: Mapping[int, float]) -> EquityPoint:
        return EquityPoint(trading_date, self._cash, 0.0, self._cash)
