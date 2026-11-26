"""Build the M6 components (sizing, risk, execution with costs and slippage) from a RunConfig.

Imported lazily by callers so the engine package never depends on the component packages at import time.
`run_backtest` uses these (and the M7 portfolio) by default.
"""
from __future__ import annotations

from dataclasses import dataclass

from algo_engine.engine.config import RunConfig
from algo_engine.engine.ports import ExecutionSimulator, OrderManager, RiskManager


@dataclass(frozen=True)
class Components:
    order_manager: OrderManager
    risk_manager: RiskManager
    executor: ExecutionSimulator


def build_components(config: RunConfig) -> Components:
    from algo_engine.execution import CostModel, FixedBpsSlippage, NextBarSimulator
    from algo_engine.orders import SizingOrderManager
    from algo_engine.risk import CashRiskManager

    return Components(
        order_manager=SizingOrderManager(config.sizing),
        risk_manager=CashRiskManager(config.risk),
        executor=NextBarSimulator(CostModel(config.costs), FixedBpsSlippage(config.slippage_bps)),
    )
