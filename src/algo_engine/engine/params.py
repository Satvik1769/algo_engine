"""Parameter dataclasses for sizing, risk, costs and slippage. Pure data: no engine imports.

All trading parameters live here as configuration with defaults (CLAUDE.md section 8.6). Cost rates are
fractions of turnover (0.001 = 0.1%) unless the field name says otherwise.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import date, datetime
from typing import Any, TypeVar

from algo_engine.config.settings import ConfigError

T = TypeVar("T")


def as_date(value: Any, key: str) -> date:
    if isinstance(value, datetime):
        raise ConfigError(f"{key} must be a date (YYYY-MM-DD), got a datetime: {value!r}")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ConfigError(f"{key} must be YYYY-MM-DD, got {value!r}") from exc
    raise ConfigError(f"{key} must be a date (YYYY-MM-DD), got {value!r}")


def _number(value: Any, key: str, *, minimum: float = 0.0, maximum: float | None = None,
            strict_min: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ConfigError(f"{key} must be a finite number, got {value!r}")
    low_ok = value > minimum if strict_min else value >= minimum
    if not low_ok or (maximum is not None and value > maximum):
        raise ConfigError(f"{key} is out of range: {value!r}")
    return float(value)


def _from_dict(cls: type[T], raw: Any, section: str) -> T:
    if not isinstance(raw, dict):
        raise ConfigError(f"{section} must be a mapping, got {type(raw).__name__}")
    names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    unknown = sorted(set(raw) - names)
    if unknown:
        raise ConfigError(f"Unknown {section} keys {unknown}; allowed: {sorted(names)}")
    return cls(**raw)


@dataclass(frozen=True)
class SizingParams:
    """Entry size = floor(min(position_pct * equity, cash) / (signal close * (1 + reserve_pct))).

    The reserve leaves room for the next-open gap, slippage and costs. Keep it >= RiskParams.cash_buffer_pct,
    otherwise a full-size order can fail the cash check.
    """
    position_pct: float = 1.0
    reserve_pct: float = 0.02

    def __post_init__(self) -> None:
        object.__setattr__(self, "position_pct", _number(self.position_pct, "sizing.position_pct",
                                                         maximum=1.0, strict_min=True))
        object.__setattr__(self, "reserve_pct", _number(self.reserve_pct, "sizing.reserve_pct", maximum=0.5))

    @classmethod
    def from_dict(cls, raw: Any) -> SizingParams:
        return _from_dict(cls, raw, "sizing")


@dataclass(frozen=True)
class RiskParams:
    cash_buffer_pct: float = 0.01      # BUY needs qty * close * (1 + buffer) <= cash not already committed today
    max_position_pct: float = 1.0      # position value after the order / equity
    max_open_positions: int = 10

    def __post_init__(self) -> None:
        object.__setattr__(self, "cash_buffer_pct", _number(self.cash_buffer_pct, "risk.cash_buffer_pct", maximum=0.5))
        object.__setattr__(self, "max_position_pct", _number(self.max_position_pct, "risk.max_position_pct",
                                                             maximum=1.0, strict_min=True))
        if not isinstance(self.max_open_positions, int) or isinstance(self.max_open_positions, bool) \
                or self.max_open_positions < 1:
            raise ConfigError(f"risk.max_open_positions must be an integer >= 1, got {self.max_open_positions!r}")

    @classmethod
    def from_dict(cls, raw: Any) -> RiskParams:
        return _from_dict(cls, raw, "risk")


@dataclass(frozen=True)
class CostSchedule:
    """Charge rates in force from `effective_from` (until the next schedule). Delivery equity, NSE.

    GST applies to brokerage + SEBI fee + exchange transaction charge. `dp_charge_per_sell` is a flat rupee
    amount per sell fill, GST included, and is a Rs 20 placeholder in the default schedule (rate unverified).
    """
    effective_from: date
    source: str = ""
    brokerage_rate: float = 0.0
    brokerage_max_per_order: float | None = None
    stt_buy_rate: float = 0.001
    stt_sell_rate: float = 0.001
    exchange_txn_rate: float = 0.0000307
    sebi_rate: float = 0.000001
    stamp_buy_rate: float = 0.00015
    gst_rate: float = 0.18
    dp_charge_per_sell: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "effective_from", as_date(self.effective_from, "costs.effective_from"))
        for key in ("brokerage_rate", "stt_buy_rate", "stt_sell_rate", "exchange_txn_rate", "sebi_rate",
                    "stamp_buy_rate", "gst_rate", "dp_charge_per_sell"):
            object.__setattr__(self, key, _number(getattr(self, key), f"costs.{key}", maximum=1e9))
        if self.brokerage_max_per_order is not None:
            object.__setattr__(self, "brokerage_max_per_order",
                               _number(self.brokerage_max_per_order, "costs.brokerage_max_per_order"))

    @classmethod
    def from_dict(cls, raw: Any) -> CostSchedule:
        return _from_dict(cls, raw, "costs schedule")

    def to_dict(self) -> dict[str, Any]:
        out = {f.name: getattr(self, f.name) for f in fields(self)}
        out["effective_from"] = self.effective_from.isoformat()
        return out

    @property
    def is_zero_cost(self) -> bool:
        rates = (self.brokerage_rate, self.stt_buy_rate, self.stt_sell_rate, self.exchange_txn_rate,
                 self.sebi_rate, self.stamp_buy_rate, self.dp_charge_per_sell)
        return all(r == 0 for r in rates)


DEFAULT_COST_SCHEDULE = CostSchedule(
    effective_from=date(2000, 1, 1),
    source=("zerodha.com/charges, fetched 2026-11-23, equity delivery NSE. Applied to the whole backtest "
            "window as an approximation (rates changed over time). DP charge on sells is a placeholder of "
            "Rs 20 per sell fill (user choice, to be confirmed)."),
    dp_charge_per_sell=20.0,
)
