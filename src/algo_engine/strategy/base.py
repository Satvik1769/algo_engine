"""Strategy interface (Milestone 5): bars in, long-only signals out.

A strategy sees one `Bar` at a time plus read-only views of history (<= the current bar) and the
portfolio. It never creates orders or touches cash; the order manager turns signals into orders that
fill from the NEXT bar (docs/ARCHITECTURE.md section 4).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from datetime import date
from enum import Enum
from typing import Any, ClassVar, Protocol

import pandas as pd


class ParamError(ValueError):
    """Raised when strategy parameters are unknown, missing or out of range."""


class Action(str, Enum):
    ENTER_LONG = "enter_long"
    EXIT_LONG = "exit_long"


@dataclass(frozen=True)
class Bar:
    fin_code: int
    trading_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


@dataclass(frozen=True)
class Signal:
    """Trading intent formed at the close of `trading_date`. Sizing is not the strategy's job."""
    fin_code: int
    trading_date: date
    action: Action
    strategy_id: str
    reason: str = ""


class HistoryView(Protocol):
    """Read-only bars with trading_date <= the current bar. The engine builds one per bar (M4)."""

    def bars(self, fin_code: int, n: int | None = None) -> pd.DataFrame:
        """Up to the last n bars (all if None), oldest first."""
        ...


class PortfolioView(Protocol):
    """Read-only portfolio state (extended in M7)."""

    def position_qty(self, fin_code: int) -> int: ...


@dataclass(frozen=True)
class StrategyContext:
    fin_codes: tuple[int, ...]


@dataclass(frozen=True)
class StrategyParams:
    """Base for typed, validated parameters. Subclasses are frozen dataclasses that validate in `__post_init__`."""

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> StrategyParams:
        raw = dict(raw or {})
        names = {f.name for f in fields(cls)}
        unknown = sorted(set(raw) - names)
        if unknown:
            raise ParamError(f"Unknown parameters {unknown}; allowed: {sorted(names)}")
        try:
            return cls(**raw)
        except TypeError as exc:  # missing required argument
            raise ParamError(str(exc)) from exc

    def as_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


class Strategy(ABC):
    name: ClassVar[str]                       # registry key, set by @register_strategy
    params_cls: ClassVar[type[StrategyParams]] = StrategyParams

    def __init__(self, params: StrategyParams) -> None:
        self.params = params

    @classmethod
    def from_config(cls, raw_params: dict[str, Any] | None = None) -> Strategy:
        return cls(cls.params_cls.from_dict(raw_params))

    def on_start(self, ctx: StrategyContext) -> None:
        """Called once before the first bar. Reset per-symbol state here."""

    @abstractmethod
    def on_bar(self, bar: Bar, history: HistoryView, portfolio: PortfolioView) -> list[Signal]:
        """Return zero or more signals using only `bar`, `history` and `portfolio`."""
