"""Execution simulator for daily bars (ADR-0006, CLAUDE.md section 6.3).

Reference price before slippage:
  MARKET     the open of the bar.
  STOP sell  triggers if low <= stop. Fills at the stop, or at the open if the bar opens at or below the stop
             (gap-through, the worse price for the trader).
  STOP buy   triggers if high >= stop. Fills at the stop, or at the open if the bar opens at or above it.
A triggered stop becomes a market order, so slippage is applied to it as well.

Order of processing within one open: SELLs first (their proceeds can fund BUYs), then BUYs, each by order id.
Sale proceeds are available the same day (T+1 settlement is not modelled). A BUY whose price plus costs exceed
the cash left is refused ("insufficient cash at fill"). A SELL larger than the position is refused.
Orders that are not triggered, or whose symbol has no bar, are left unfilled and expire.
There is no volume cap in the MVP: every triggered order fills in full.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from algo_engine.engine.events import OrderType, Side
from algo_engine.engine.ports import AccountView, ExecutionReport, Fill, Order
from algo_engine.execution.costs import CostModel
from algo_engine.execution.slippage import FixedBpsSlippage
from algo_engine.strategy import Bar


def reference_price(order: Order, bar: Bar) -> float | None:
    """Price before slippage, or None if the order is not triggered on this bar."""
    if order.order_type is OrderType.MARKET:
        return bar.open
    stop = order.stop_price
    assert stop is not None  # enforced by Order
    if order.side is Side.SELL:
        if bar.low > stop:
            return None
        return bar.open if bar.open <= stop else stop
    if bar.high < stop:
        return None
    return bar.open if bar.open >= stop else stop


class NextBarSimulator:
    def __init__(self, cost_model: CostModel, slippage: FixedBpsSlippage) -> None:
        self._costs = cost_model
        self._slippage = slippage

    def execute(self, orders: Sequence[Order], bars: Mapping[int, Bar], trading_date: date,
                account: AccountView) -> ExecutionReport:
        cash = account.cash()
        held: dict[int, int] = {}
        fills: list[Fill] = []
        rejected: dict[int, str] = {}
        for order in sorted(orders, key=lambda o: (o.side is not Side.SELL, o.order_id)):
            bar = bars.get(order.fin_code)
            if bar is None:
                continue
            ref = reference_price(order, bar)
            if ref is None:
                continue
            price = self._slippage.adjust(order.side, ref)
            costs = self._costs.cost(order.side, order.quantity, price, trading_date)
            gross = order.quantity * price
            if order.side is Side.SELL:
                position = held.setdefault(order.fin_code, account.position_qty(order.fin_code))
                if order.quantity > position:
                    rejected[order.order_id] = f"sell {order.quantity} exceeds position {position}"
                    continue
                held[order.fin_code] = position - order.quantity
                cash += gross - costs.total
            else:
                need = gross + costs.total
                if need > cash:
                    rejected[order.order_id] = f"insufficient cash at fill: need {need:.2f}, have {cash:.2f}"
                    continue
                cash -= need
            fills.append(Fill(order.order_id, order.fin_code, order.side, order.quantity, price, trading_date,
                              costs.total, costs.as_dict()))
        fills.sort(key=lambda f: f.order_id)
        return ExecutionReport(fills, rejected)
