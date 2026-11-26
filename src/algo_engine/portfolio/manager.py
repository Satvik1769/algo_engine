"""Average-cost portfolio for a long-only cash account (Milestone 7).

Accounting conventions (all in rupees, prices are the fill prices that already include slippage):
  BUY  fill: cash -= quantity * price + costs.   The position's average price is the gross average of the
             buys (costs are NOT folded into it); the buy costs are kept apart and charged to the trade.
  SELL fill: cash += quantity * price - costs.   Gross P&L of the sold shares = (price - average price) * quantity.
  A position keeps its average price when shares are sold, and changes it only on a BUY (weighted average).
  Costs are counted once: in cash when the fill is applied, and in the trade's `costs` when the position closes.
A round trip starts with the first BUY of a flat symbol and ends when the position returns to zero. Partial
sells inside a round trip belong to the same trade. The trade log records one row per closed round trip.

Dividends: `apply_dividend` credits amount_per_share * shares held at that moment to cash and to the open round
trip. The loop calls it at the start of the ex-date, before that day's fills, so a position held at the previous
close is paid (a buy filled on the ex-date is not, a sell filled on the ex-date still is). No tax or TDS.

Invariants (checked by the tests): cash never goes below zero (a fill that would overdraw raises), a SELL never
exceeds the position, `equity = cash + sum(quantity * close)`, and
`equity - initial_cash = net P&L of closed trades + open gross P&L - open costs + open dividends`,
where a trade's `net_pnl = gross_pnl - costs + dividends`.

Positions still open at the end of a run are not in the trade log; they are valued at the last close.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from algo_engine.engine.errors import ExecutionError
from algo_engine.engine.events import Side
from algo_engine.engine.ports import EquityPoint, Fill

_CASH_TOLERANCE = 1e-6


@dataclass(frozen=True)
class Trade:
    """One closed round trip."""
    fin_code: int
    entry_date: date
    exit_date: date
    quantity: int            # total shares bought during the round trip
    entry_price: float       # average buy price
    exit_price: float        # average sell price
    gross_pnl: float         # sum of (sell price - average cost) * quantity sold
    costs: float             # buy and sell costs of the round trip
    net_pnl: float           # gross_pnl - costs + dividends
    holding_days: int        # calendar days between entry and exit
    dividends: float = 0.0   # cash dividends received during the round trip


@dataclass
class _Position:
    opened_on: date
    quantity: int = 0
    avg_price: float = 0.0
    bought_qty: int = 0
    bought_value: float = 0.0
    sold_qty: int = 0
    sold_value: float = 0.0
    gross_pnl: float = 0.0
    costs: float = 0.0
    dividends: float = 0.0


class AverageCostPortfolio:
    def __init__(self, initial_cash: float) -> None:
        if not initial_cash > 0:
            raise ValueError(f"initial_cash must be positive, got {initial_cash!r}")
        self.initial_cash = float(initial_cash)
        self._cash = float(initial_cash)
        self._positions: dict[int, _Position] = {}
        self._last_close: dict[int, float] = {}
        self._equity = float(initial_cash)
        self._trades: list[Trade] = []
        self._total_costs = 0.0
        self._total_dividends = 0.0

    # AccountView
    def position_qty(self, fin_code: int) -> int:
        pos = self._positions.get(fin_code)
        return pos.quantity if pos else 0

    def cash(self) -> float:
        return self._cash

    def equity(self) -> float:
        return self._equity

    def open_positions(self) -> int:
        return len(self._positions)

    # Portfolio
    def apply_fill(self, fill: Fill) -> None:
        if fill.quantity <= 0 or not fill.price > 0 or fill.costs < 0:
            raise ExecutionError(f"Order {fill.order_id}: invalid fill (quantity {fill.quantity}, price "
                                 f"{fill.price}, costs {fill.costs})")
        if fill.side is Side.BUY:
            self._buy(fill)
        else:
            self._sell(fill)
        self._total_costs += fill.costs

    def _buy(self, fill: Fill) -> None:
        cash_after = self._cash - (fill.quantity * fill.price + fill.costs)
        if cash_after < -_CASH_TOLERANCE:
            raise ExecutionError(f"Order {fill.order_id}: BUY would overdraw cash ({self._cash:.2f} available)")
        pos = self._positions.get(fill.fin_code)
        if pos is None:
            pos = self._positions[fill.fin_code] = _Position(opened_on=fill.trading_date)
        pos.avg_price = (pos.quantity * pos.avg_price + fill.quantity * fill.price) / (pos.quantity + fill.quantity)
        pos.quantity += fill.quantity
        pos.bought_qty += fill.quantity
        pos.bought_value += fill.quantity * fill.price
        pos.costs += fill.costs
        self._cash = cash_after

    def _sell(self, fill: Fill) -> None:
        pos = self._positions.get(fill.fin_code)
        held = pos.quantity if pos else 0
        if fill.quantity > held:
            raise ExecutionError(f"Order {fill.order_id}: SELL {fill.quantity} exceeds position {held}")
        assert pos is not None
        pos.gross_pnl += (fill.price - pos.avg_price) * fill.quantity
        pos.quantity -= fill.quantity
        pos.sold_qty += fill.quantity
        pos.sold_value += fill.quantity * fill.price
        pos.costs += fill.costs
        self._cash += fill.quantity * fill.price - fill.costs
        if pos.quantity == 0:
            self._trades.append(Trade(
                fin_code=fill.fin_code, entry_date=pos.opened_on, exit_date=fill.trading_date,
                quantity=pos.bought_qty, entry_price=pos.bought_value / pos.bought_qty,
                exit_price=pos.sold_value / pos.sold_qty, gross_pnl=pos.gross_pnl, costs=pos.costs,
                net_pnl=pos.gross_pnl - pos.costs + pos.dividends,
                holding_days=(fill.trading_date - pos.opened_on).days, dividends=pos.dividends))
            del self._positions[fill.fin_code]

    def apply_dividend(self, fin_code: int, amount_per_share: float, trading_date: date) -> float:
        if not amount_per_share > 0:
            raise ExecutionError(f"Dividend for {fin_code} on {trading_date}: amount must be positive")
        pos = self._positions.get(fin_code)
        if pos is None:
            return 0.0
        cash = pos.quantity * amount_per_share
        pos.dividends += cash
        self._cash += cash
        self._total_dividends += cash
        return cash

    def mark_to_market(self, trading_date: date, closes: Mapping[int, float]) -> EquityPoint:
        value = 0.0
        for code, pos in sorted(self._positions.items()):
            if code not in closes or not closes[code] > 0:
                raise ExecutionError(f"No valid close for held symbol {code} on {trading_date}")
            self._last_close[code] = closes[code]
            value += pos.quantity * closes[code]
        self._equity = self._cash + value
        return EquityPoint(trading_date, self._cash, value, self._equity)

    # Read-only extras
    @property
    def trades(self) -> list[Trade]:
        return list(self._trades)

    @property
    def total_costs(self) -> float:
        """Costs of every fill applied so far (open and closed round trips)."""
        return self._total_costs

    @property
    def total_dividends(self) -> float:
        return self._total_dividends

    def average_price(self, fin_code: int) -> float | None:
        pos = self._positions.get(fin_code)
        return pos.avg_price if pos else None

    def open_costs(self) -> float:
        """Costs charged so far to round trips that are still open."""
        return sum(p.costs for p in self._positions.values())

    def open_dividends(self) -> float:
        """Dividends received so far in round trips that are still open."""
        return sum(p.dividends for p in self._positions.values())

    def open_pnl(self) -> float:
        """Unrealized plus realized-but-not-yet-closed gross P&L of open round trips, at the last marks."""
        return sum(p.gross_pnl + p.quantity * (self._last_close.get(c, p.avg_price) - p.avg_price)
                   for c, p in self._positions.items())
