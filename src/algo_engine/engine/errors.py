"""Typed errors raised by the engine. Nothing in the loop swallows them."""
from __future__ import annotations


class EngineError(RuntimeError):
    """Base class for engine failures."""


class BarDataError(EngineError):
    """The bars handed to the engine are unusable (missing columns, duplicates, unsorted, empty, bad prices)."""


class SignalError(EngineError):
    """A strategy returned a signal that breaks the contract (unknown symbol, wrong date)."""


class ExecutionError(EngineError):
    """An order manager, risk manager or executor broke the order lifecycle rules."""


class LookaheadError(EngineError):
    """A strategy produced different signals when given only the data up to a bar."""
