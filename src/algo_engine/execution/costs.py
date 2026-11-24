"""Transaction cost model for equity delivery (CNC) on NSE.

For a fill with turnover V = quantity * price (all rates come from the `CostSchedule` in force on the date):

    brokerage = min(brokerage_rate * V, brokerage_max_per_order)   (cap only if set)
    stt       = V * stt_buy_rate (BUY) or V * stt_sell_rate (SELL)
    exchange  = V * exchange_txn_rate
    sebi      = V * sebi_rate
    stamp     = V * stamp_buy_rate                                  (BUY only)
    gst       = gst_rate * (brokerage + sebi + exchange)
    dp        = dp_charge_per_sell                                  (SELL only, flat, GST included)
    total     = sum of the above

Amounts are not rounded to paise: the total reconciles exactly with the cash movement in the portfolio.
Slippage is separate (it changes the fill price, not the charges).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from algo_engine.engine.errors import ExecutionError
from algo_engine.engine.events import Side
from algo_engine.engine.params import CostSchedule


@dataclass(frozen=True)
class CostBreakdown:
    brokerage: float
    stt: float
    exchange_txn: float
    sebi: float
    stamp: float
    gst: float
    dp: float

    @property
    def total(self) -> float:
        return self.brokerage + self.stt + self.exchange_txn + self.sebi + self.stamp + self.gst + self.dp

    def as_dict(self) -> dict[str, float]:
        return {"brokerage": self.brokerage, "stt": self.stt, "exchange_txn": self.exchange_txn,
                "sebi": self.sebi, "stamp": self.stamp, "gst": self.gst, "dp": self.dp}


class CostModel:
    def __init__(self, schedules: Sequence[CostSchedule]) -> None:
        if not schedules:
            raise ValueError("CostModel needs at least one schedule")
        self._schedules = sorted(schedules, key=lambda s: s.effective_from)

    def schedule_on(self, trading_date: date) -> CostSchedule:
        chosen = None
        for s in self._schedules:
            if s.effective_from <= trading_date:
                chosen = s
        if chosen is None:
            raise ExecutionError(f"No cost schedule is effective on {trading_date}")
        return chosen

    def cost(self, side: Side, quantity: int, price: float, trading_date: date) -> CostBreakdown:
        s = self.schedule_on(trading_date)
        turnover = quantity * price
        brokerage = s.brokerage_rate * turnover
        if s.brokerage_max_per_order is not None:
            brokerage = min(brokerage, s.brokerage_max_per_order)
        buy = side is Side.BUY
        sebi = turnover * s.sebi_rate
        exchange = turnover * s.exchange_txn_rate
        return CostBreakdown(
            brokerage=brokerage,
            stt=turnover * (s.stt_buy_rate if buy else s.stt_sell_rate),
            exchange_txn=exchange,
            sebi=sebi,
            stamp=turnover * s.stamp_buy_rate if buy else 0.0,
            gst=s.gst_rate * (brokerage + sebi + exchange),
            dp=0.0 if buy else s.dp_charge_per_sell,
        )
