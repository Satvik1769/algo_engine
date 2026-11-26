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
    # optional, with defaults (see engine/params.py): sizing, risk, costs (list of dated schedules), slippage_bps

The snapshot (`to_dict`) is written to the run directory and hashed into the run id.
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from algo_engine.config.settings import ConfigError
from algo_engine.engine.params import (
    DEFAULT_COST_SCHEDULE, CostSchedule, RiskParams, SizingParams, as_date,
)
from algo_engine.strategy import create_strategy

_ALLOWED_KEYS = {"symbols", "start", "end", "strategy", "initial_capital", "seed", "price_basis",
                 "sizing", "risk", "costs", "slippage_bps"}
_REQUIRED_KEYS = {"symbols", "start", "end", "strategy"}
_PRICE_BASES = ("adjusted",)  # the bar store holds corporate-action adjusted prices only (ADR-0010)


@dataclass(frozen=True)
class RunConfig:
    symbols: tuple[int, ...]
    start: date
    end: date
    strategy: dict[str, Any] = field(default_factory=dict)
    initial_capital: float = 1_000_000.0
    seed: int = 0
    price_basis: str = "adjusted"
    sizing: SizingParams = field(default_factory=SizingParams)
    risk: RiskParams = field(default_factory=RiskParams)
    costs: tuple[CostSchedule, ...] = (DEFAULT_COST_SCHEDULE,)
    slippage_bps: float = 5.0

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
        costs = tuple(self.costs)
        if not costs:
            raise ConfigError("costs must contain at least one schedule")
        dates = [c.effective_from for c in costs]
        if len(set(dates)) != len(dates):
            raise ConfigError("costs schedules must have distinct effective_from dates")
        if min(dates) > self.start:
            raise ConfigError(f"No cost schedule is effective on the start date {self.start} "
                              f"(earliest effective_from is {min(dates)})")
        object.__setattr__(self, "costs", tuple(sorted(costs, key=lambda c: c.effective_from)))
        bps = self.slippage_bps
        if isinstance(bps, bool) or not isinstance(bps, (int, float)) or not math.isfinite(bps) \
                or bps < 0 or bps > 1000:
            raise ConfigError(f"slippage_bps must be between 0 and 1000, got {bps!r}")
        object.__setattr__(self, "slippage_bps", float(bps))
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
            "start": as_date(raw["start"], "start"),
            "end": as_date(raw["end"], "end"),
            "strategy": copy.deepcopy(raw["strategy"]),
        }
        for key in ("initial_capital", "seed", "price_basis", "slippage_bps"):
            if key in raw:
                kwargs[key] = raw[key]
        if "sizing" in raw:
            kwargs["sizing"] = SizingParams.from_dict(raw["sizing"])
        if "risk" in raw:
            kwargs["risk"] = RiskParams.from_dict(raw["risk"])
        if "costs" in raw:
            if not isinstance(raw["costs"], (list, tuple)):
                raise ConfigError(f"costs must be a list of schedules, got {raw['costs']!r}")
            kwargs["costs"] = tuple(CostSchedule.from_dict(c) for c in raw["costs"])
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
            "sizing": {"position_pct": self.sizing.position_pct, "reserve_pct": self.sizing.reserve_pct},
            "risk": {"cash_buffer_pct": self.risk.cash_buffer_pct, "max_position_pct": self.risk.max_position_pct,
                     "max_open_positions": self.risk.max_open_positions},
            "costs": [c.to_dict() for c in self.costs],
            "slippage_bps": self.slippage_bps,
        }

    def snapshot_json(self) -> str:
        """Canonical JSON text of the config (sorted keys), the form stored and hashed."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n"
