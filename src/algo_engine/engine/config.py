"""Run configuration: one validated, immutable description of a backtest, loadable from YAML.

Example (configs/ema_crossover_nifty50.yaml):

    symbols: [100256265]
    start: 2018-01-01
    end: 2023-12-31
    initial_capital: 1000000
    seed: 0
    strategy:
      name: ema_crossover
      params: {fast: 12, slow: 26}

The snapshot (`to_dict`) is written to the run directory and hashed into the run id.
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from algo_engine.config.settings import ConfigError
from algo_engine.strategy import create_strategy

_ALLOWED_KEYS = {"symbols", "start", "end", "strategy", "initial_capital", "seed", "price_basis"}
_REQUIRED_KEYS = {"symbols", "start", "end", "strategy"}
_PRICE_BASES = ("adjusted",)  # the bar store holds corporate-action adjusted prices only (ADR-0010)


def _as_date(value: Any, key: str) -> date:
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


@dataclass(frozen=True)
class RunConfig:
    symbols: tuple[int, ...]
    start: date
    end: date
    strategy: dict[str, Any] = field(default_factory=dict)
    initial_capital: float = 1_000_000.0
    seed: int = 0
    price_basis: str = "adjusted"

    def __post_init__(self) -> None:
        symbols = tuple(self.symbols)
        if not symbols:
            raise ConfigError("symbols must not be empty")
        if any(not isinstance(s, int) or isinstance(s, bool) for s in symbols):
            raise ConfigError(f"symbols must be integer fin_codes, got {symbols!r}")
        if len(set(symbols)) != len(symbols):
            raise ConfigError(f"symbols contains duplicates: {symbols!r}")
        object.__setattr__(self, "symbols", symbols)
        if self.start > self.end:
            raise ConfigError(f"start {self.start} is after end {self.end}")
        capital = self.initial_capital
        if isinstance(capital, bool) or not isinstance(capital, (int, float)) \
                or not math.isfinite(capital) or capital <= 0:
            raise ConfigError(f"initial_capital must be a positive number, got {capital!r}")
        object.__setattr__(self, "initial_capital", float(capital))
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise ConfigError(f"seed must be an integer, got {self.seed!r}")
        if self.price_basis not in _PRICE_BASES:
            raise ConfigError(f"price_basis must be one of {_PRICE_BASES}, got {self.price_basis!r}")
        # Fail early on unknown strategies or bad parameters (raises ParamError).
        create_strategy(copy.deepcopy(self.strategy))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RunConfig:
        if not isinstance(raw, dict):
            raise ConfigError(f"Run config must be a mapping, got {type(raw).__name__}")
        unknown = sorted(set(raw) - _ALLOWED_KEYS)
        if unknown:
            raise ConfigError(f"Unknown run config keys {unknown}; allowed: {sorted(_ALLOWED_KEYS)}")
        missing = sorted(_REQUIRED_KEYS - set(raw))
        if missing:
            raise ConfigError(f"Missing run config keys: {missing}")
        symbols = raw["symbols"]
        if not isinstance(symbols, (list, tuple)):
            raise ConfigError(f"symbols must be a list, got {symbols!r}")
        kwargs: dict[str, Any] = {
            "symbols": tuple(symbols),
            "start": _as_date(raw["start"], "start"),
            "end": _as_date(raw["end"], "end"),
            "strategy": copy.deepcopy(raw["strategy"]),
        }
        for key in ("initial_capital", "seed", "price_basis"):
            if key in raw:
                kwargs[key] = raw[key]
        return cls(**kwargs)

    @classmethod
    def from_yaml(cls, path: str | Path) -> RunConfig:
        with open(path, encoding="utf-8") as handle:
            try:
                raw = yaml.safe_load(handle)
            except yaml.YAMLError as exc:
                raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
        return cls.from_dict(raw)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbols": list(self.symbols),
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "strategy": copy.deepcopy(self.strategy),
            "initial_capital": self.initial_capital,
            "seed": self.seed,
            "price_basis": self.price_basis,
        }

    def snapshot_json(self) -> str:
        """Canonical JSON text of the config (sorted keys), the form stored and hashed."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n"
