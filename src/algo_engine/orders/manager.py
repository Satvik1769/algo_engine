"""Signal-to-order sizing (long-only, delivery, market orders).

ENTER_LONG: quantity = floor(min(position_pct * equity, cash) / (close * (1 + reserve_pct))), where `close` is the
            close of the signal bar, the only price known when the order is formed. The reserve covers the
            next-open gap, slippage and costs.
EXIT_LONG:  sell the whole position.
A signal that cannot become an order is reported in `SizingResult.skipped` with a reason:
already long, no position to exit, or quantity rounds to zero.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date

from algo_engine.engine.events import Side
from algo_engine.engine.params import SizingParams
from algo_engine.engine.ports import AccountView, Order, SizingResult, SkippedSignal
from algo_engine.strategy import Action, Bar, Signal


class SizingOrderManager:
    def __init__(self, params: SizingParams) -> None:
        self._params = params
        self._next_id = 1

    def create_orders(self, signals: Sequence[Signal], account: AccountView, bars: Mapping[int, Bar],
                      trading_date: date) -> SizingResult:
        orders: list[Order] = []
        skipped: list[SkippedSignal] = []
        for sig in signals:
            held = account.position_qty(sig.fin_code)
            if sig.action is Action.ENTER_LONG:
                if held > 0:
                    skipped.append(SkippedSignal(sig, f"already long {held}"))
                    continue
                budget = min(self._params.position_pct * account.equity(), account.cash())
                qty = math.floor(budget / (bars[sig.fin_code].close * (1.0 + self._params.reserve_pct)))
                if qty < 1:
                    skipped.append(SkippedSignal(sig, "quantity rounds to zero"))
                    continue
                side = Side.BUY
            else:
                if held <= 0:
                    skipped.append(SkippedSignal(sig, "no position to exit"))
                    continue
                qty, side = held, Side.SELL
            orders.append(Order(self._next_id, sig.fin_code, side, qty, trading_date, sig.strategy_id))
            self._next_id += 1
        return SizingResult(orders, skipped)
