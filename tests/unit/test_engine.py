"""Engine loop (M4): ordering, truncation, determinism, calendar handling, look-ahead guard, config."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from algo_engine.config.settings import ConfigError
from algo_engine.engine import (
    BarDataError, DataQualityEvent, EventLog, ExecutionError, FillEvent, LookaheadError, MarketEvent,
    OrderEvent, OrderStatus, PortfolioUpdateEvent, RiskRejectionEvent, RunConfig, Side, SignalError,
    SignalEvent, assert_no_lookahead, find_lookahead, run_backtest,
)
from algo_engine.engine.history import SymbolSeries, TruncatedHistory
from algo_engine.engine.ports import (
    CashOnlyPortfolio, ExecutionReport, Fill, Order, RiskDecision, SizingResult,
)
from algo_engine.strategy import (
    Action, Bar, ParamError, Signal, Strategy, StrategyContext, StrategyParams, register_strategy,
)

# ---------------------------------------------------------------- helpers


def make_bars(closes: list[float], fin_code: int = 1, start: str = "2024-01-01",
              skip: Sequence[int] = ()) -> pd.DataFrame:
    """Daily bars on business days; `skip` lists positions (0-based) to leave out."""
    dates = pd.bdate_range(start, periods=len(closes)).date
    df = pd.DataFrame({
        "fin_code": fin_code, "trading_date": dates, "open": closes,
        "high": [c + 1 for c in closes], "low": [c - 1 for c in closes], "close": closes, "volume": 1000.0,
    })
    return df.drop(index=list(skip)).reset_index(drop=True)


def config(strategy: dict | None = None, symbols=(1,), start="2024-01-01", end="2024-12-31", **kw) -> RunConfig:
    return RunConfig.from_dict({"symbols": list(symbols), "start": start, "end": end,
                                "strategy": strategy or {"name": "noop"}, **kw})


@dataclass(frozen=True)
class ScriptParams(StrategyParams):
    enter_dates: list = field(default_factory=list)
    symbol: int = 0  # 0 = any symbol
    bad: str = ""    # "symbol" or "date" makes the signal invalid on purpose


@register_strategy("test_script")
class ScriptStrategy(Strategy):
    """Emits ENTER_LONG on listed dates; records every on_bar call."""
    params_cls = ScriptParams
    calls: list[tuple[int, date]] = []

    def on_start(self, ctx: StrategyContext) -> None:
        type(self).calls = []

    def on_bar(self, bar, history, portfolio):
        type(self).calls.append((bar.fin_code, bar.trading_date))
        p = self.params
        if bar.trading_date.isoformat() not in p.enter_dates or p.symbol not in (0, bar.fin_code):
            return []
        code = 999 if p.bad == "symbol" else bar.fin_code
        day = date(2000, 1, 1) if p.bad == "date" else bar.trading_date
        return [Signal(code, day, Action.ENTER_LONG, self.name, "scripted")]


@register_strategy("test_cheat")
class CheatStrategy(Strategy):
    """Reaches past the view into the engine's private full series and buys when tomorrow's close is higher."""

    def on_bar(self, bar, history, portfolio):
        full = history._series[bar.fin_code].frame  # type: ignore[attr-defined]
        later = full[full["trading_date"] > bar.trading_date]
        if len(later) and later.iloc[0]["close"] > bar.close:
            return [Signal(bar.fin_code, bar.trading_date, Action.ENTER_LONG, self.name, "peek")]
        return []


@register_strategy("test_mutator")
class MutatorStrategy(Strategy):
    """Tries to change the portfolio. The engine must hand over a view without apply_fill."""

    def on_bar(self, bar, history, portfolio):
        portfolio.apply_fill(None)  # type: ignore[attr-defined]
        return []


class BuyOneManager:
    """Test order manager: one BUY of 1 share per ENTER_LONG signal."""

    def __init__(self) -> None:
        self.next_id = 1

    def create_orders(self, signals, account, bars, trading_date):
        orders = []
        for s in signals:
            orders.append(Order(self.next_id, s.fin_code, Side.BUY, 1, trading_date, s.strategy_id))
            self.next_id += 1
        return SizingResult(orders)


class OpenFillExecutor:
    """Test executor: fills every order whose symbol has a bar, at that bar's open. Records offers."""

    def __init__(self) -> None:
        self.offers: list[tuple[date, int]] = []

    def execute(self, orders: Sequence[Order], bars: Mapping[int, Bar], trading_date: date, account=None):
        fills = []
        for o in orders:
            self.offers.append((trading_date, o.order_id))
            if o.fin_code in bars:
                fills.append(Fill(o.order_id, o.fin_code, o.side, o.quantity, bars[o.fin_code].open, trading_date))
        return ExecutionReport(fills)


class RecordingPortfolio(CashOnlyPortfolio):
    def __init__(self, cash: float) -> None:
        super().__init__(cash)
        self.fills: list[Fill] = []
        self.marks: list[tuple[date, dict[int, float]]] = []

    def apply_fill(self, fill: Fill) -> None:
        self.fills.append(fill)

    def mark_to_market(self, trading_date, closes):
        self.marks.append((trading_date, dict(closes)))
        return super().mark_to_market(trading_date, closes)


def kinds(events, day: date) -> list[str]:
    return [type(e).__name__ for e in events if e.trading_date == day]


# ---------------------------------------------------------------- config


def test_config_loads_from_yaml(tmp_path: Path):
    path = tmp_path / "c.yaml"
    path.write_text("symbols: [1, 2]\nstart: 2018-01-01\nend: 2018-12-31\nseed: 7\n"
                    "strategy: {name: ema_crossover, params: {fast: 3, slow: 5}}\n")
    cfg = RunConfig.from_yaml(path)
    assert cfg.symbols == (1, 2) and cfg.start == date(2018, 1, 1) and cfg.seed == 7
    assert cfg.initial_capital == 1_000_000.0 and cfg.price_basis == "adjusted"
    assert RunConfig.from_dict(cfg.to_dict()) == cfg


def test_repo_example_configs_load():
    root = Path(__file__).resolve().parents[2] / "configs"
    for name in ("noop_nifty50_index.yaml", "ema_crossover_nifty50_index.yaml"):
        assert RunConfig.from_yaml(root / name).symbols == (100256265,)


@pytest.mark.parametrize("raw, exc", [
    ({"symbols": [1], "start": "2024-01-01", "end": "2024-02-01", "strategy": {"name": "noop"}, "oops": 1}, ConfigError),
    ({"symbols": [1], "start": "2024-01-01", "strategy": {"name": "noop"}}, ConfigError),
    ({"symbols": [], "start": "2024-01-01", "end": "2024-02-01", "strategy": {"name": "noop"}}, ConfigError),
    ({"symbols": [1, 1], "start": "2024-01-01", "end": "2024-02-01", "strategy": {"name": "noop"}}, ConfigError),
    ({"symbols": [1], "start": "2024-03-01", "end": "2024-02-01", "strategy": {"name": "noop"}}, ConfigError),
    ({"symbols": [1], "start": "01/01/2024", "end": "2024-02-01", "strategy": {"name": "noop"}}, ConfigError),
    ({"symbols": [1], "start": "2024-01-01", "end": "2024-02-01", "strategy": {"name": "noop"},
      "initial_capital": 0}, ConfigError),
    ({"symbols": [1], "start": "2024-01-01", "end": "2024-02-01", "strategy": {"name": "nope"}}, ParamError),
    ({"symbols": [1], "start": "2024-01-01", "end": "2024-02-01",
      "strategy": {"name": "ema_crossover", "params": {"fast": 9, "slow": 3}}}, ParamError),
])
def test_config_rejects_bad_input(raw, exc):
    with pytest.raises(exc):
        RunConfig.from_dict(raw)


# ---------------------------------------------------------------- history truncation


def test_history_hides_future_bars_for_every_symbol():
    a, b = make_bars([10, 11, 12, 13], 1), make_bars([20, 21, 22, 23], 2)
    series = {1: SymbolSeries(a), 2: SymbolSeries(b)}
    t = a["trading_date"].iloc[1]
    view = TruncatedHistory(series, t)
    for code in (1, 2):
        got = view.bars(code)
        assert list(got["close"]) == ([10, 11] if code == 1 else [20, 21])
        assert got["trading_date"].max() <= t
    assert list(view.bars(1, 1)["close"]) == [11]
    assert view.bars(1, 0).empty
    copy = view.bars(1)
    copy["close"] = -1  # a copy: the engine's data is unchanged
    assert list(view.bars(1)["close"]) == [10, 11]


# ---------------------------------------------------------------- loop, events, determinism


def test_noop_run_keeps_capital_and_orders_events_by_phase():
    bars = make_bars([10, 11, 12])
    res = run_backtest(config(), bars)
    assert res.calendar_days == 3
    assert list(res.equity["equity"]) == [1_000_000.0] * 3
    day = bars["trading_date"].iloc[0]
    assert kinds(res.events, day) == ["MarketEvent", "PortfolioUpdateEvent"]
    assert [e.seq for e in res.events] == list(range(len(res.events)))


def test_runs_are_byte_identical_and_run_id_tracks_inputs(tmp_path: Path):
    bars = make_bars([10, 11, 12, 13, 12, 14, 15])
    cfg = config({"name": "ema_crossover", "params": {"fast": 2, "slow": 3}})
    r1 = run_backtest(cfg, bars, output_dir=tmp_path / "a")
    r2 = run_backtest(cfg, bars.copy(), output_dir=tmp_path / "b")
    assert r1.run_id == r2.run_id and r1.data_fingerprint == r2.data_fingerprint
    names = sorted(p.name for p in r1.output_dir.iterdir())
    assert names == ["config.json", "equity.csv", "events.jsonl", "manifest.json", "trades.csv"]
    for name in names:
        assert (r1.output_dir / name).read_bytes() == (r2.output_dir / name).read_bytes()
    other_cfg = run_backtest(config({"name": "ema_crossover", "params": {"fast": 2, "slow": 4}}), bars)
    other_data = run_backtest(cfg, make_bars([10, 11, 12, 13, 12, 14, 16]))
    assert len({r1.run_id, other_cfg.run_id, other_data.run_id}) == 3


def test_custom_sink_receives_the_same_events():
    seen = EventLog()
    res = run_backtest(config(), make_bars([10, 11]), sink=seen)
    assert [e.to_dict() for e in seen.events] == [e.to_dict() for e in res.events]


def test_strategy_gets_a_read_only_portfolio():
    with pytest.raises(AttributeError):
        run_backtest(config({"name": "test_mutator"}), make_bars([10, 11]))


# ---------------------------------------------------------------- signals


def test_ema_crossover_signals_flow_through_the_engine():
    closes = [10, 10, 10, 10, 11, 12, 13, 12, 10, 8, 7, 7]
    res = run_backtest(config({"name": "ema_crossover", "params": {"fast": 2, "slow": 3}}), make_bars(closes))
    assert [s.action for s in res.signals] == [Action.ENTER_LONG]  # no position stub, so no exit while flat
    events = [e for e in res.events if isinstance(e, SignalEvent)]
    assert [e.action for e in events] == ["enter_long"]
    assert events[0].trading_date == res.signals[0].trading_date


@pytest.mark.parametrize("bad, exc", [("symbol", SignalError), ("date", SignalError)])
def test_invalid_signals_raise(bad, exc):
    bars = make_bars([10, 11, 12])
    cfg = config({"name": "test_script", "params": {"enter_dates": [bars["trading_date"].iloc[0].isoformat()],
                                                     "bad": bad}})
    with pytest.raises(exc):
        run_backtest(cfg, bars)


# ---------------------------------------------------------------- order lifecycle


def _scripted(bars: pd.DataFrame, *positions: int, **extra) -> RunConfig:
    dates = [bars["trading_date"].iloc[i].isoformat() for i in positions]
    return config({"name": "test_script", "params": {"enter_dates": dates, **extra}})


def test_order_created_at_t_is_first_offered_and_filled_on_the_next_bar():
    bars = make_bars([10, 11, 12, 13])
    d = list(bars["trading_date"])
    ex, pf = OpenFillExecutor(), RecordingPortfolio(1_000_000)
    res = run_backtest(_scripted(bars, 1), bars, order_manager=BuyOneManager(), executor=ex, portfolio=pf)
    assert ex.offers == [(d[2], 1)]  # signal at d[1] close, offered at d[2]
    assert [(f.trading_date, f.price) for f in pf.fills] == [(d[2], 12.0)]  # next open
    assert kinds(res.events, d[1]) == ["MarketEvent", "PortfolioUpdateEvent", "SignalEvent", "OrderEvent", "OrderEvent"]
    assert kinds(res.events, d[2]) == ["FillEvent", "OrderEvent", "MarketEvent", "PortfolioUpdateEvent"]
    statuses = [e.status for e in res.events if isinstance(e, OrderEvent)]
    assert statuses == [OrderStatus.CREATED, OrderStatus.ACCEPTED, OrderStatus.FILLED]
    fill_event = next(e for e in res.events if isinstance(e, FillEvent))
    assert fill_event.trading_date > next(e for e in res.events if isinstance(e, SignalEvent)).trading_date


def test_no_event_on_a_date_refers_to_a_later_date():
    bars = make_bars([10, 11, 12, 13, 14])
    res = run_backtest(_scripted(bars, 0, 2), bars, order_manager=BuyOneManager(), executor=OpenFillExecutor(),
                       portfolio=RecordingPortfolio(1e6))
    for e in res.events:
        if isinstance(e, MarketEvent):
            row = bars[(bars["fin_code"] == e.fin_code) & (bars["trading_date"] == e.trading_date)].iloc[0]
            assert e.close == row["close"]
    fills = [e for e in res.events if isinstance(e, FillEvent)]
    signals = [e for e in res.events if isinstance(e, SignalEvent)]
    assert all(f.trading_date > min(s.trading_date for s in signals) for f in fills)


def test_unfilled_day_order_expires():
    bars = make_bars([10, 11, 12, 13])

    class NeverFills:
        def execute(self, orders, bars, trading_date, account):
            return ExecutionReport()

    res = run_backtest(_scripted(bars, 0), bars, order_manager=BuyOneManager(), executor=NeverFills())
    statuses = [e.status for e in res.events if isinstance(e, OrderEvent)]
    assert statuses == [OrderStatus.CREATED, OrderStatus.ACCEPTED, OrderStatus.EXPIRED]


def test_risk_rejection_is_logged_and_order_never_offered():
    bars = make_bars([10, 11, 12])

    class RejectAll:
        def validate(self, order, account, bar):
            return RiskDecision(False, "insufficient cash")

    ex = OpenFillExecutor()
    res = run_backtest(_scripted(bars, 0), bars, order_manager=BuyOneManager(), risk_manager=RejectAll(),
                       executor=ex)
    assert ex.offers == []
    rejection = next(e for e in res.events if isinstance(e, RiskRejectionEvent))
    assert rejection.reason == "insufficient cash"
    assert [e.status for e in res.events if isinstance(e, OrderEvent)] == [OrderStatus.CREATED, OrderStatus.REJECTED]


@pytest.mark.parametrize("make_fill", [
    lambda o, t: Fill(o.order_id + 100, o.fin_code, o.side, o.quantity, 1.0, t),            # unknown order
    lambda o, t: Fill(o.order_id, o.fin_code, o.side, o.quantity, 1.0, date(2000, 1, 1)),   # wrong date
    lambda o, t: Fill(o.order_id, o.fin_code, o.side, o.quantity + 1, 1.0, t),              # partial/oversize
])
def test_bad_fills_raise(make_fill):
    bars = make_bars([10, 11, 12])

    class BadExecutor:
        def execute(self, orders, bars, trading_date, account):
            return ExecutionReport([make_fill(orders[0], trading_date)])

    with pytest.raises(ExecutionError):
        run_backtest(_scripted(bars, 0), bars, order_manager=BuyOneManager(), executor=BadExecutor(),
                     portfolio=RecordingPortfolio(1e6))


def test_order_manager_cannot_backdate_or_use_a_symbol_without_a_bar():
    bars = make_bars([10, 11, 12])

    class Backdated:
        def create_orders(self, signals, account, bars, trading_date):
            return SizingResult([Order(1, 1, Side.BUY, 1, date(2000, 1, 1), "x")])

    with pytest.raises(ExecutionError):
        run_backtest(_scripted(bars, 0), bars, order_manager=Backdated())


# ---------------------------------------------------------------- calendar and bar data


def test_multi_symbol_calendar_alignment_and_missing_bar_policy():
    a = make_bars([10, 11, 12, 13, 14], 1)
    b = make_bars([20, 21, 22, 23, 24], 2, skip=[2])           # day 2 missing in the middle
    late = make_bars([30, 31], 3, start="2024-01-04")           # lists late: not "missing" before listing
    bars = pd.concat([a, b, late]).sort_values(["fin_code", "trading_date"]).reset_index(drop=True)
    pf = RecordingPortfolio(1e6)
    res = run_backtest(config(symbols=(1, 2, 3)), bars, portfolio=pf)
    d = list(a["trading_date"])
    assert res.calendar_days == 5
    dq = [e for e in res.events if isinstance(e, DataQualityEvent)]
    assert [(e.trading_date, e.fin_code, e.kind) for e in dq] == [(d[2], 2, "missing_bar")]
    # carried at its last close on the missing day, and nothing is invented for symbols not yet listed
    marks = dict(pf.marks)
    assert marks[d[2]] == {1: 12.0, 2: 21.0}
    assert marks[d[3]][2] == 23.0 and marks[d[3]][3] == 30.0
    market = [(e.trading_date, e.fin_code) for e in res.events if isinstance(e, MarketEvent)]
    assert (d[2], 2) not in market and len(market) == 5 + 4 + 2


def test_strategy_is_not_called_for_a_missing_bar():
    a = make_bars([10, 11, 12], 1)
    b = make_bars([20, 21, 22], 2, skip=[1])
    bars = pd.concat([a, b]).sort_values(["fin_code", "trading_date"]).reset_index(drop=True)
    run_backtest(config({"name": "test_script"}, symbols=(1, 2)), bars)
    d = list(a["trading_date"])
    assert (2, d[1]) not in ScriptStrategy.calls and (1, d[1]) in ScriptStrategy.calls
    assert ScriptStrategy.calls == sorted(ScriptStrategy.calls, key=lambda c: (c[1], c[0]))


def test_bars_are_filtered_to_symbols_and_dates():
    bars = pd.concat([make_bars([10, 11, 12, 13], 1), make_bars([5, 5, 5, 5], 9)], ignore_index=True)
    res = run_backtest(config(start="2024-01-02", end="2024-01-03"), bars)
    assert res.calendar_days == 2
    assert {e.fin_code for e in res.events if isinstance(e, MarketEvent)} == {1}


@pytest.mark.parametrize("mutate", [
    lambda d: pd.concat([d, d.iloc[[0]]], ignore_index=True),                       # duplicate row
    lambda d: d.iloc[::-1].reset_index(drop=True),                                  # unsorted
    lambda d: d.drop(columns=["volume"]),                                           # missing column
    lambda d: d.assign(close=[10.0, None, 12.0]),                                   # null price
    lambda d: d.assign(open=[10.0, 0.0, 12.0]),                                     # non-positive price
])
def test_bad_bar_data_raises(mutate):
    with pytest.raises(BarDataError):
        run_backtest(config(), mutate(make_bars([10, 11, 12])))


def test_empty_range_and_absent_symbol_raise():
    bars = make_bars([10, 11, 12])
    with pytest.raises(BarDataError):
        run_backtest(config(start="2030-01-01", end="2030-02-01"), bars)
    with pytest.raises(BarDataError):
        run_backtest(config(symbols=(1, 2)), bars)


def test_strategy_errors_are_not_swallowed():
    class Boom(Exception):
        pass

    @register_strategy("test_boom")
    class BoomStrategy(Strategy):
        def on_bar(self, bar, history, portfolio):
            raise Boom("strategy failed")

    with pytest.raises(Boom):
        run_backtest(config({"name": "test_boom"}), make_bars([10, 11]))


# ---------------------------------------------------------------- look-ahead guard


def test_guard_passes_a_causal_strategy():
    closes = [10, 10, 10, 10, 11, 12, 13, 12, 10, 8, 7, 7, 8, 9, 11, 12]
    cfg = config({"name": "ema_crossover", "params": {"fast": 2, "slow": 3}})
    bars = make_bars(closes)
    assert len(run_backtest(cfg, bars).signals) > 0  # the check is not vacuous
    assert find_lookahead(cfg, bars) == []
    assert_no_lookahead(cfg, bars)


def test_guard_flags_a_strategy_that_peeks_ahead():
    bars = make_bars([10, 12, 11, 13, 12, 14, 13, 15])
    cfg = config({"name": "test_cheat"})
    assert len(run_backtest(cfg, bars).signals) > 0
    assert find_lookahead(cfg, bars) != []
    with pytest.raises(LookaheadError):
        assert_no_lookahead(cfg, bars)
