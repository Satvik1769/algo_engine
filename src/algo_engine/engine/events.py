"""Immutable events written to the run log (docs/ARCHITECTURE.md section 4).

Components never call each other through events. The engine loop owns the ordering and passes events to an
`EventSink`. In a backtest the sink is an `EventLog`; paper/live runners can supply a sink that also
forwards events elsewhere without any change to strategy, order, risk or portfolio code.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from datetime import date
from enum import Enum
from typing import Any, Protocol


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    CREATED = "created"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FILLED = "filled"
    EXPIRED = "expired"


@dataclass(frozen=True)
class Event:
    """Base event. `seq` is assigned by the sink (monotonic, starting at 0)."""
    trading_date: date
    seq: int = field(default=-1, kw_only=True)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": type(self).__name__}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, Enum):
                value = value.value
            elif isinstance(value, date):
                value = value.isoformat()
            out[f.name] = value
        return out


@dataclass(frozen=True)
class MarketEvent(Event):
    """A bar closed. Published at the close of `trading_date`."""
    fin_code: int
    open: float
    high: float
    low: float
    close: float
    volume: float | None


@dataclass(frozen=True)
class SignalEvent(Event):
    fin_code: int
    action: str
    strategy_id: str
    reason: str


@dataclass(frozen=True)
class OrderEvent(Event):
    """One order state transition."""
    order_id: int
    fin_code: int
    side: Side
    quantity: int
    status: OrderStatus
    reason: str = ""


@dataclass(frozen=True)
class FillEvent(Event):
    order_id: int
    fin_code: int
    side: Side
    quantity: int
    price: float
    costs: float


@dataclass(frozen=True)
class RiskRejectionEvent(Event):
    order_id: int
    fin_code: int
    reason: str


@dataclass(frozen=True)
class PortfolioUpdateEvent(Event):
    cash: float
    positions_value: float
    equity: float


@dataclass(frozen=True)
class DataQualityEvent(Event):
    """A data problem the run met (for example a missing bar inside a symbol's history)."""
    fin_code: int
    kind: str
    detail: str


class EventSink(Protocol):
    def emit(self, event: Event) -> None: ...


class EventLog:
    """In-memory sink that stamps each event with the next sequence number."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def emit(self, event: Event) -> None:
        self.events.append(replace(event, seq=len(self.events)))
