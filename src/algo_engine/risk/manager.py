"""Pre-trade risk checks for a long-only cash account. Every rejection carries a reason.

SELL: quantity must not exceed the position minus sells already accepted on the same date.
BUY:  quantity * close * (1 + cash_buffer_pct) must fit in the cash not already committed to other BUYs
      accepted on the same date; the position value after the order must not exceed max_position_pct of equity;
      the number of open positions plus new symbols already accepted today must stay below max_open_positions.
The manager remembers what it accepted for the current date only, so same-day orders see each other.
"""
from __future__ import annotations

from datetime import date

from algo_engine.engine.events import Side
from algo_engine.engine.params import RiskParams
from algo_engine.engine.ports import AccountView, Order, RiskDecision
from algo_engine.strategy import Bar


class CashRiskManager:
    def __init__(self, params: RiskParams) -> None:
        self._params = params
        self._day: date | None = None
        self._committed_cash = 0.0
        self._new_symbols: set[int] = set()
        self._sold: dict[int, int] = {}

    def _roll(self, day: date) -> None:
        if day != self._day:
            self._day, self._committed_cash = day, 0.0
            self._new_symbols, self._sold = set(), {}

    def validate(self, order: Order, account: AccountView, bar: Bar) -> RiskDecision:
        self._roll(order.created_on)
        p = self._params
        held = account.position_qty(order.fin_code)
        if order.side is Side.SELL:
            available = held - self._sold.get(order.fin_code, 0)
            if order.quantity > available:
                return RiskDecision(False, f"sell {order.quantity} exceeds position {available}")
            self._sold[order.fin_code] = self._sold.get(order.fin_code, 0) + order.quantity
            return RiskDecision(True)

        notional = order.quantity * bar.close
        need = notional * (1.0 + p.cash_buffer_pct)
        free_cash = account.cash() - self._committed_cash
        if need > free_cash:
            return RiskDecision(False, f"insufficient cash: need {need:.2f}, free {free_cash:.2f}")
        equity = account.equity()
        if equity <= 0 or (held * bar.close + notional) / equity > p.max_position_pct:
            return RiskDecision(False, f"position would exceed {p.max_position_pct:.0%} of equity")
        is_new = held == 0 and order.fin_code not in self._new_symbols
        if is_new and account.open_positions() + len(self._new_symbols) >= p.max_open_positions:
            return RiskDecision(False, f"max open positions ({p.max_open_positions}) reached")
        self._committed_cash += need
        if held == 0:
            self._new_symbols.add(order.fin_code)
        return RiskDecision(True)
