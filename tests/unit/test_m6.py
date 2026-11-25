"""M6: order state machine, sizing, risk, costs, slippage, execution, and the wired pipeline."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from algo_engine.config.settings import ConfigError
from algo_engine.engine import (
    ExecutionError, FillEvent, OrderEvent, OrderStatus, RiskRejectionEvent, RunConfig, Side, SignalEvent,
    SignalSkippedEvent, run_backtest,
)
from algo_engine.engine.build import build_components
from algo_engine.engine.events import OrderType
from algo_engine.engine.orderbook import OrderBook
from algo_engine.engine.params import CostSchedule, RiskParams, SizingParams
from algo_engine.engine.ports import EquityPoint, Fill, Order
from algo_engine.execution import CostModel, FixedBpsSlippage, NextBarSimulator, reference_price
from algo_engine.orders import SizingOrderManager
from algo_engine.risk import CashRiskManager
from algo_engine.strategy import Action, Bar, Signal, Strategy, register_strategy

D1, D2 = date(2024, 1, 1), date(2024, 1, 2)
SCHEDULE = CostSchedule(effective_from=date(2000, 1, 1))


@dataclass
class Account:
    """Stand-in account for unit tests."""
    cash_: float = 1_000_000.0
    equity_: float | None = None
    qty: dict[int, int] = field(default_factory=dict)

    def position_qty(self, fin_code: int) -> int:
        return self.qty.get(fin_code, 0)

    def cash(self) -> float:
        return self.cash_

    def equity(self) -> float:
        return self.cash_ if self.equity_ is None else self.equity_

    def open_positions(self) -> int:
        return sum(1 for q in self.qty.values() if q > 0)


def bar(close=100.0, open_=None, high=None, low=None, fin_code=1, day=D2) -> Bar:
    o = close if open_ is None else open_
    return Bar(fin_code, day, o, high if high is not None else max(o, close) + 1,
               low if low is not None else min(o, close) - 1, close, 1000.0)


def order(oid=1, side=Side.BUY, qty=10, otype=OrderType.MARKET, stop=None, fin_code=1, created=D1) -> Order:
    return Order(oid, fin_code, side, qty, created, "s", otype, stop)


def simulator(bps=5.0, schedule=SCHEDULE) -> NextBarSimulator:
    return NextBarSimulator(CostModel([schedule]), FixedBpsSlippage(bps))


# ---------------------------------------------------------------- costs and slippage (hand calculations)


def test_buy_costs_match_hand_calculation():
    # turnover 100 * 500 = 50,000
    c = CostModel([SCHEDULE]).cost(Side.BUY, 100, 500.0, D1)
    assert c.brokerage == 0
    assert c.stt == pytest.approx(50.0)                 # 0.1%
    assert c.exchange_txn == pytest.approx(1.535)       # 0.00307%
    assert c.sebi == pytest.approx(0.05)                # Rs 10 per crore
    assert c.stamp == pytest.approx(7.5)                # 0.015% on buy
    assert c.gst == pytest.approx(0.2853)               # 18% of (0 + 0.05 + 1.535)
    assert c.dp == 0
    assert c.total == pytest.approx(59.3703)


def test_sell_costs_have_no_stamp_and_include_dp_when_configured():
    sell = CostModel([SCHEDULE]).cost(Side.SELL, 100, 500.0, D1)
    assert sell.stamp == 0 and sell.total == pytest.approx(51.8703)
    with_dp = CostModel([CostSchedule(effective_from=date(2000, 1, 1), dp_charge_per_sell=15.93)])
    assert with_dp.cost(Side.SELL, 100, 500.0, D1).total == pytest.approx(51.8703 + 15.93)
    assert with_dp.cost(Side.BUY, 100, 500.0, D1).dp == 0


def test_brokerage_cap_and_gst_base():
    sched = CostSchedule(effective_from=date(2000, 1, 1), brokerage_rate=0.0003, brokerage_max_per_order=20.0)
    c = CostModel([sched]).cost(Side.BUY, 1000, 1000.0, D1)   # turnover 1,000,000: 0.03% = 300, capped at 20
    assert c.brokerage == 20.0
    assert c.gst == pytest.approx(0.18 * (20.0 + 1_000_000 * 0.000001 + 1_000_000 * 0.0000307))


def test_cost_schedule_is_chosen_by_effective_date():
    old = CostSchedule(effective_from=date(2018, 1, 1), stt_buy_rate=0.002)
    new = CostSchedule(effective_from=date(2022, 1, 1), stt_buy_rate=0.001)
    model = CostModel([new, old])
    assert model.cost(Side.BUY, 1, 100.0, date(2020, 1, 1)).stt == pytest.approx(0.2)
    assert model.cost(Side.BUY, 1, 100.0, date(2022, 1, 1)).stt == pytest.approx(0.1)
    with pytest.raises(ExecutionError):
        model.cost(Side.BUY, 1, 100.0, date(2017, 1, 1))


def test_slippage_is_always_against_the_trader():
    s = FixedBpsSlippage(5)
    assert s.adjust(Side.BUY, 100.0) == pytest.approx(100.05)
    assert s.adjust(Side.SELL, 100.0) == pytest.approx(99.95)
    assert FixedBpsSlippage(0).adjust(Side.BUY, 100.0) == 100.0
    with pytest.raises(ValueError):
        FixedBpsSlippage(-1)


# ---------------------------------------------------------------- orders and the state machine


@pytest.mark.parametrize("kwargs", [
    {"qty": 0}, {"qty": -5}, {"otype": OrderType.STOP}, {"otype": OrderType.STOP, "stop": 0.0},
    {"otype": OrderType.MARKET, "stop": 90.0},
])
def test_invalid_orders_are_refused(kwargs):
    with pytest.raises(ExecutionError):
        order(**kwargs)


def test_order_state_machine_allows_only_legal_transitions():
    book = OrderBook()
    o = order()
    book.register(o)
    assert book.status(1) is OrderStatus.CREATED
    with pytest.raises(ExecutionError):
        book.transition(1, OrderStatus.FILLED)       # must be accepted first
    book.transition(1, OrderStatus.ACCEPTED)
    book.transition(1, OrderStatus.FILLED)
    for new in OrderStatus:
        with pytest.raises(ExecutionError):
            book.transition(1, new)                  # FILLED is terminal
    with pytest.raises(ExecutionError):
        book.register(o)                             # duplicate id
    with pytest.raises(ExecutionError):
        book.status(99)


@pytest.mark.parametrize("path", [
    [OrderStatus.REJECTED], [OrderStatus.ACCEPTED, OrderStatus.EXPIRED], [OrderStatus.ACCEPTED, OrderStatus.REJECTED],
])
def test_terminal_states_are_final(path):
    book = OrderBook()
    book.register(order())
    for status in path:
        book.transition(1, status)
    for new in OrderStatus:
        with pytest.raises(ExecutionError):
            book.transition(1, new)


# ---------------------------------------------------------------- sizing


def sig(action, fin_code=1) -> Signal:
    return Signal(fin_code, D1, action, "s")


def test_entry_size_matches_hand_calculation():
    mgr = SizingOrderManager(SizingParams(position_pct=1.0, reserve_pct=0.02))
    res = mgr.create_orders([sig(Action.ENTER_LONG)], Account(1_000_000), {1: bar(500.0, day=D1)}, D1)
    assert [(o.side, o.quantity, o.order_id, o.created_on) for o in res.orders] == [(Side.BUY, 1960, 1, D1)]
    half = SizingOrderManager(SizingParams(position_pct=0.5, reserve_pct=0.02))
    assert half.create_orders([sig(Action.ENTER_LONG)], Account(1_000_000), {1: bar(500.0)}, D1).orders[0].quantity == 980
    cash_bound = SizingOrderManager(SizingParams())
    acct = Account(cash_=100_000, equity_=1_000_000)
    assert cash_bound.create_orders([sig(Action.ENTER_LONG)], acct, {1: bar(500.0)}, D1).orders[0].quantity == 196


def test_unactionable_signals_are_skipped_with_a_reason():
    mgr = SizingOrderManager(SizingParams())
    bars = {1: bar(500.0)}
    long_acct = Account(qty={1: 10})
    assert mgr.create_orders([sig(Action.ENTER_LONG)], long_acct, bars, D1).skipped[0].reason == "already long 10"
    assert mgr.create_orders([sig(Action.EXIT_LONG)], Account(), bars, D1).skipped[0].reason == "no position to exit"
    tiny = mgr.create_orders([sig(Action.ENTER_LONG)], Account(100.0), bars, D1)
    assert tiny.orders == [] and tiny.skipped[0].reason == "quantity rounds to zero"
    exit_all = mgr.create_orders([sig(Action.EXIT_LONG)], long_acct, bars, D1)
    assert [(o.side, o.quantity) for o in exit_all.orders] == [(Side.SELL, 10)]
    assert exit_all.orders[0].order_id == 1  # earlier calls created no orders, so ids start at 1


# ---------------------------------------------------------------- risk


def test_risk_rejects_insufficient_cash_and_commits_cash_within_a_day():
    risk = CashRiskManager(RiskParams(cash_buffer_pct=0.0))
    acct = Account(cash_=1_000.0)
    b = bar(100.0)
    assert risk.validate(order(1, qty=6), acct, b).accepted             # 600 of 1000
    second = risk.validate(order(2, qty=6, fin_code=2), acct, b)
    assert not second.accepted and "insufficient cash" in second.reason
    next_day = order(3, qty=6, created=D2)
    assert risk.validate(next_day, acct, b).accepted                    # commitments reset on a new date


def test_risk_enforces_position_size_and_open_position_limits():
    b = bar(100.0)
    big = CashRiskManager(RiskParams(max_position_pct=0.5, cash_buffer_pct=0.0))
    d = big.validate(order(qty=6), Account(1_000.0), b)
    assert not d.accepted and "50%" in d.reason
    risk = CashRiskManager(RiskParams(max_open_positions=2, cash_buffer_pct=0.0))
    acct = Account(10_000.0, qty={9: 5})
    assert risk.validate(order(1, qty=1, fin_code=1), acct, b).accepted        # 2nd position
    third = risk.validate(order(2, qty=1, fin_code=2), acct, b)
    assert not third.accepted and "max open positions" in third.reason
    assert risk.validate(order(3, qty=1, fin_code=9), acct, b).accepted        # adding to an existing holding


def test_risk_rejects_sells_beyond_the_position():
    risk = CashRiskManager(RiskParams())
    acct = Account(qty={1: 10})
    assert risk.validate(order(1, Side.SELL, 6), acct, bar()).accepted
    assert not risk.validate(order(2, Side.SELL, 6), acct, bar()).accepted     # only 4 left today
    assert not risk.validate(order(3, Side.SELL, 1, fin_code=2), acct, bar()).accepted


# ---------------------------------------------------------------- execution


def test_market_order_fills_at_open_with_slippage_and_costs():
    rep = simulator(5).execute([order(qty=100)], {1: bar(close=102.0, open_=100.0)}, D2, Account())
    (fill,) = rep.fills
    assert fill.price == pytest.approx(100.05) and fill.trading_date == D2
    turnover = 100 * 100.05
    expected = turnover * (0.001 + 0.0000307 + 0.000001 + 0.00015) + 0.18 * turnover * (0.0000307 + 0.000001)
    assert fill.costs == pytest.approx(expected)
    assert fill.costs == pytest.approx(sum(fill.cost_breakdown.values()))
    assert rep.rejected == {}


@pytest.mark.parametrize("open_, low, expected_ref", [
    (100.0, 94.0, 95.0),    # trades through the stop intraday: fill at the stop
    (90.0, 88.0, 90.0),     # gaps through the stop: fill at the open
    (95.0, 93.0, 95.0),     # opens exactly at the stop
    (100.0, 96.0, None),    # never reaches the stop
])
def test_sell_stop_semantics(open_, low, expected_ref):
    o = order(1, Side.SELL, 10, OrderType.STOP, stop=95.0)
    b = bar(close=open_, open_=open_, low=low, high=open_ + 1)
    assert reference_price(o, b) == expected_ref
    rep = simulator(0).execute([o], {1: b}, D2, Account(qty={1: 10}))
    assert [f.price for f in rep.fills] == ([] if expected_ref is None else [expected_ref])


@pytest.mark.parametrize("open_, high, expected_ref", [
    (100.0, 106.0, 105.0), (108.0, 109.0, 108.0), (100.0, 104.0, None),
])
def test_buy_stop_semantics(open_, high, expected_ref):
    o = order(1, Side.BUY, 1, OrderType.STOP, stop=105.0)
    assert reference_price(o, bar(close=open_, open_=open_, high=high, low=open_ - 1)) == expected_ref


def test_triggered_stop_is_slipped_like_a_market_order():
    o = order(1, Side.SELL, 10, OrderType.STOP, stop=95.0)
    rep = simulator(10).execute([o], {1: bar(close=96.0, open_=100.0, low=94.0)}, D2, Account(qty={1: 10}))
    assert rep.fills[0].price == pytest.approx(95.0 * 0.999)


def test_insufficient_cash_at_fill_is_refused_not_overdrawn():
    # accepted against a 100 close, but the next open gapped to 110
    rep = simulator(0).execute([order(qty=100)], {1: bar(close=110.0, open_=110.0)}, D2, Account(cash_=10_050.0))
    assert rep.fills == [] and "insufficient cash at fill" in rep.rejected[1]


def test_sells_are_processed_before_buys_so_proceeds_fund_entries():
    buy, sell = order(1, Side.BUY, 5, fin_code=2), order(2, Side.SELL, 10, fin_code=1)
    bars = {1: bar(100.0, fin_code=1), 2: bar(100.0, fin_code=2)}
    rep = simulator(0).execute([buy, sell], bars, D2, Account(cash_=0.0, qty={1: 10}))
    assert [f.order_id for f in rep.fills] == [1, 2] and rep.rejected == {}


def test_oversized_sell_is_refused_and_missing_bar_or_untriggered_order_is_left_alone():
    rep = simulator().execute([order(1, Side.SELL, 11)], {1: bar()}, D2, Account(qty={1: 10}))
    assert rep.fills == [] and "exceeds position" in rep.rejected[1]
    rep = simulator().execute([order(1, fin_code=7)], {1: bar()}, D2, Account())
    assert rep.fills == [] and rep.rejected == {}


# ---------------------------------------------------------------- config


def test_config_defaults_and_roundtrip_with_custom_sections():
    base = {"symbols": [1], "start": "2024-01-01", "end": "2024-06-01", "strategy": {"name": "noop"}}
    cfg = RunConfig.from_dict(base)
    assert cfg.slippage_bps == 5.0 and cfg.sizing == SizingParams() and cfg.risk == RiskParams()
    assert len(cfg.costs) == 1 and cfg.costs[0].dp_charge_per_sell == 20.0 and "zerodha.com/charges" in cfg.costs[0].source
    custom = RunConfig.from_dict({**base, "slippage_bps": 10, "sizing": {"position_pct": 0.5},
                                  "risk": {"max_open_positions": 3},
                                  "costs": [{"effective_from": "2018-01-01", "stt_buy_rate": 0.002},
                                            {"effective_from": "2020-07-01", "stamp_buy_rate": 0.00015}]})
    assert RunConfig.from_dict(custom.to_dict()) == custom
    assert custom.costs[0].effective_from < custom.costs[1].effective_from


@pytest.mark.parametrize("extra", [
    {"sizing": {"nope": 1}}, {"sizing": {"position_pct": 0}}, {"sizing": {"position_pct": 1.5}},
    {"risk": {"max_open_positions": 0}}, {"risk": {"cash_buffer_pct": -0.1}},
    {"slippage_bps": -1}, {"slippage_bps": "5"}, {"costs": []},
    {"costs": [{"effective_from": "2025-01-01"}]},                                    # starts after run start
    {"costs": [{"effective_from": "2018-01-01"}, {"effective_from": "2018-01-01"}]},  # duplicate date
    {"costs": [{"effective_from": "2018-01-01", "stt_buy_rate": -1}]},
    {"costs": [{"effective_from": "2018-01-01", "bogus": 1}]},
])
def test_config_rejects_bad_m6_sections(extra):
    raw = {"symbols": [1], "start": "2024-01-01", "end": "2024-06-01", "strategy": {"name": "noop"}, **extra}
    with pytest.raises(ConfigError):
        RunConfig.from_dict(raw)


# ---------------------------------------------------------------- wired pipeline (with a minimal test portfolio)


class LedgerPortfolio:
    """Just enough accounting to exercise M6 end to end. The real portfolio is M7."""

    def __init__(self, cash: float) -> None:
        self._cash = cash
        self.qty: dict[int, int] = {}
        self.fills: list[Fill] = []
        self.min_cash = cash
        self._equity = cash

    def position_qty(self, fin_code):
        return self.qty.get(fin_code, 0)

    def cash(self):
        return self._cash

    def equity(self):
        return self._equity

    def open_positions(self):
        return sum(1 for q in self.qty.values() if q > 0)

    def apply_fill(self, fill: Fill) -> None:
        sign = 1 if fill.side is Side.BUY else -1
        self._cash -= sign * fill.quantity * fill.price + fill.costs if sign == 1 else \
            -(fill.quantity * fill.price - fill.costs)
        self.qty[fill.fin_code] = self.qty.get(fill.fin_code, 0) + sign * fill.quantity
        self.min_cash = min(self.min_cash, self._cash)
        self.fills.append(fill)

    def mark_to_market(self, trading_date, closes):
        value = sum(q * closes[c] for c, q in self.qty.items())
        self._equity = self._cash + value
        return EquityPoint(trading_date, self._cash, value, self._equity)


def swing_bars() -> pd.DataFrame:
    """Smooth swings (daily moves under 1.5%), so next-open gaps stay inside the sizing reserve."""
    closes = [round(100 + 4 * math.sin(i / 3), 2) for i in range(48)]
    dates = pd.bdate_range("2024-01-01", periods=len(closes)).date
    return pd.DataFrame({"fin_code": 1, "trading_date": dates, "open": [c - 0.2 for c in closes],
                         "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
                         "close": closes, "volume": 1000.0})


def jumpy_bars() -> pd.DataFrame:
    closes = [10, 10, 10, 10, 11, 13, 15, 16, 15, 13, 10, 8, 7, 7, 8, 10, 12, 14, 13, 11, 9, 8, 8, 9]
    dates = pd.bdate_range("2024-01-01", periods=len(closes)).date
    return pd.DataFrame({"fin_code": 1, "trading_date": dates, "open": [c - 0.2 for c in closes],
                         "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
                         "close": closes, "volume": 1000.0})


def cfg_swing(**extra) -> RunConfig:
    return RunConfig.from_dict({"symbols": [1], "start": "2024-01-01", "end": "2024-12-31",
                                "strategy": {"name": "ema_crossover", "params": {"fast": 2, "slow": 3}}, **extra})


def run_wired(cfg: RunConfig, output_dir=None, bars=None):
    comps = build_components(cfg)
    pf = LedgerPortfolio(cfg.initial_capital)
    res = run_backtest(cfg, swing_bars() if bars is None else bars, order_manager=comps.order_manager, risk_manager=comps.risk_manager,
                       executor=comps.executor, portfolio=pf, output_dir=output_dir)
    return res, pf


def test_wired_pipeline_first_trade_matches_hand_calculation():
    res, pf = run_wired(cfg_swing())
    bars = swing_bars()
    first_signal = next(e for e in res.events if isinstance(e, SignalEvent))
    close_t = bars.loc[bars["trading_date"] == first_signal.trading_date, "close"].iloc[0]
    next_open = bars[bars["trading_date"] > first_signal.trading_date].iloc[0]
    buy = pf.fills[0]
    assert buy.side is Side.BUY and buy.trading_date == next_open["trading_date"] > first_signal.trading_date
    assert buy.quantity == int(1_000_000 // (close_t * 1.02))
    assert buy.price == pytest.approx(next_open["open"] * 1.0005)
    turnover = buy.quantity * buy.price
    assert buy.costs == pytest.approx(
        turnover * (0.001 + 0.0000307 + 0.000001 + 0.00015) + 0.18 * turnover * (0.0000307 + 0.000001))
    sells = [f for f in pf.fills if f.side is Side.SELL]
    assert sells and sells[0].quantity == buy.quantity            # exits the whole position


def test_wired_pipeline_invariants_and_reconciliation():
    res, pf = run_wired(cfg_swing())
    assert len(pf.fills) >= 2
    assert pf.min_cash >= 0                                        # cash never negative
    assert all(q >= 0 for q in pf.qty.values())                    # long-only
    signal_dates = [e.trading_date for e in res.events if isinstance(e, SignalEvent)]
    fills = [e for e in res.events if isinstance(e, FillEvent)]
    assert all(f.trading_date > min(signal_dates) for f in fills)
    created = {e.order_id: e.trading_date for e in res.events
               if isinstance(e, OrderEvent) and e.status is OrderStatus.CREATED}
    assert all(f.trading_date > created[f.order_id] for f in fills)   # fill is on a later bar than creation
    assert all(f.costs == pytest.approx(sum(f.cost_breakdown.values())) for f in fills)
    # cash reconciles to the initial capital, trades and costs
    expected = 1_000_000.0
    for f in pf.fills:
        gross = f.quantity * f.price
        expected += (-gross - f.costs) if f.side is Side.BUY else (gross - f.costs)
    assert pf.cash() == pytest.approx(expected)
    total_costs = sum(f.costs for f in pf.fills)
    last_close = swing_bars()["close"].iloc[-1]
    assert res.equity["equity"].iloc[-1] == pytest.approx(
        1_000_000.0 + sum((f.quantity * f.price) * (1 if f.side is Side.SELL else -1) for f in pf.fills)
        + pf.qty.get(1, 0) * last_close - total_costs)
    # every order ends in a terminal state
    final = {}
    for e in res.events:
        if isinstance(e, OrderEvent):
            final[e.order_id] = e.status
    assert set(final.values()) <= {OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}


def test_wired_pipeline_logs_skips_rejections_and_is_deterministic(tmp_path: Path):
    tight = cfg_swing(risk={"max_position_pct": 0.1}, sizing={"position_pct": 1.0})
    res, pf = run_wired(tight)
    assert pf.fills == []
    assert any(isinstance(e, RiskRejectionEvent) and "10%" in e.reason for e in res.events)
    a, _ = run_wired(cfg_swing(), tmp_path / "a")
    b, _ = run_wired(cfg_swing(), tmp_path / "b")
    for name in ("config.json", "events.jsonl", "equity.csv", "manifest.json"):
        assert (a.output_dir / name).read_bytes() == (b.output_dir / name).read_bytes()
    manifest = json.loads((a.output_dir / "manifest.json").read_text())
    assert manifest["zero_cost_diagnostic"] is False and manifest["slippage_bps"] == 5.0
    zero = cfg_swing(costs=[{"effective_from": "2000-01-01", "stt_buy_rate": 0, "stt_sell_rate": 0,
                             "exchange_txn_rate": 0, "sebi_rate": 0, "stamp_buy_rate": 0}], slippage_bps=0)
    z, _ = run_wired(zero, tmp_path / "z")
    assert json.loads((z.output_dir / "manifest.json").read_text())["zero_cost_diagnostic"] is True


def test_overnight_gap_beyond_the_reserve_is_refused_at_fill_not_overdrawn():
    res, pf = run_wired(cfg_swing(), bars=jumpy_bars())
    assert pf.fills == [] and pf.min_cash == 1_000_000.0
    refused = [e for e in res.events if isinstance(e, OrderEvent) and e.status is OrderStatus.REJECTED]
    assert refused and all("insufficient cash at fill" in e.reason for e in refused)


@register_strategy("test_m6_always_enter")
class AlwaysEnter(Strategy):
    def on_bar(self, bar, history, portfolio):
        return [Signal(bar.fin_code, bar.trading_date, Action.ENTER_LONG, self.name, "always")]


def test_skipped_signals_reach_the_event_log():
    cfg = RunConfig.from_dict({"symbols": [1], "start": "2024-01-01", "end": "2024-12-31",
                               "strategy": {"name": "test_m6_always_enter"}})
    comps = build_components(cfg)
    pf = LedgerPortfolio(cfg.initial_capital)
    pf.qty[1] = 5  # already long, so every enter signal is skipped
    res = run_backtest(cfg, swing_bars().head(3), order_manager=comps.order_manager,
                       risk_manager=comps.risk_manager, executor=comps.executor, portfolio=pf)
    skipped = [e for e in res.events if isinstance(e, SignalSkippedEvent)]
    assert len(skipped) == 3 and {e.reason for e in skipped} == {"already long 5"}
    assert not any(isinstance(e, OrderEvent) for e in res.events)
