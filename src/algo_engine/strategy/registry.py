"""Strategy registry: add a strategy by decorating the class; no engine edits needed."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from algo_engine.strategy.base import ParamError, Strategy

_REGISTRY: dict[str, type[Strategy]] = {}


def register_strategy(name: str) -> Callable[[type[Strategy]], type[Strategy]]:
    def decorator(cls: type[Strategy]) -> type[Strategy]:
        if name in _REGISTRY and _REGISTRY[name] is not cls:
            raise ValueError(f"Strategy name already registered: {name!r}")
        cls.name = name
        _REGISTRY[name] = cls
        return cls
    return decorator


def available_strategies() -> list[str]:
    return sorted(_REGISTRY)


def create_strategy(config: dict[str, Any]) -> Strategy:
    """Build a strategy from `{"name": "...", "params": {...}}`. Unknown names and params raise."""
    unknown_keys = sorted(set(config) - {"name", "params"})
    if unknown_keys:
        raise ParamError(f"Unknown strategy config keys: {unknown_keys}")
    name = config.get("name")
    if name not in _REGISTRY:
        raise ParamError(f"Unknown strategy {name!r}; available: {available_strategies()}")
    return _REGISTRY[name].from_config(config.get("params"))
