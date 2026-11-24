"""The chronological backtest loop (docs/ARCHITECTURE.md section 4).

For each date `t` of the union calendar, in this fixed order:
  1. Open phase: orders accepted on earlier bars go to the execution simulator with bar `t`. Fills go to the
     portfolio. Day orders that did not fill expire.
  2. Close phase: a MarketEvent per bar, then the portfolio is marked to market at the close of `t`.
  3. The strategy sees bar `t`, history up to `t`, and a read-only portfolio, and returns signals.
  4. The order manager turns signals into orders, risk validates them, accepted orders wait for `t+1`.

The loop owns the ordering. Components never call each other. An order created at `t` can only reach the
executor in a later iteration, and the loop raises if a component breaks that rule.

Missing bars: a symbol with no bar on `t` between its first and last bar gets a DataQualityEvent, receives
no `on_bar` call, and is valued at its last close (carry with a flag, never a silent forward-fill).
Dates before a symbol's first bar or after its last bar are not treated as missing.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from algo_engine.engine.config import RunConfig
from algo_engine.engine.errors import BarDataError, ExecutionError, SignalError
from algo_engine.engine.events import (
    DataQualityEvent, Event, EventLog, EventSink, FillEvent, MarketEvent, OrderEvent, OrderStatus,
    PortfolioUpdateEvent, RiskRejectionEvent, SignalEvent,
)
from algo_engine.engine.history import SymbolSeries, TruncatedHistory
from algo_engine.engine.ports import (
    AcceptAllRisk, CashOnlyPortfolio, EquityPoint, ExecutionSimulator, NoFillExecution, NoOpOrderManager,
    Order, OrderManager, Portfolio, RiskManager,
)
from algo_engine.strategy import Bar, PortfolioView, Signal, StrategyContext, create_strategy

ENGINE_COLUMNS = ["fin_code", "trading_date", "open", "high", "low", "close", "volume"]
_PRICE_COLUMNS = ["open", "high", "low", "close"]


@dataclass(frozen=True)
class RunResult:
    config: RunConfig
    run_id: str
    data_fingerprint: str
    events: list[Event]
    signals: list[Signal]
    equity: pd.DataFrame
    calendar_days: int
    output_dir: Path | None = None


class _ReadOnlyPortfolio:
    """What the strategy gets: positions only, no way to apply fills."""

    def __init__(self, portfolio: PortfolioView) -> None:
        self._portfolio = portfolio

    def position_qty(self, fin_code: int) -> int:
        return self._portfolio.position_qty(fin_code)


def prepare_bars(config: RunConfig, bars: pd.DataFrame) -> pd.DataFrame:
    """Select the run's symbols and dates and check them. Raises BarDataError, never repairs."""
    missing_cols = [c for c in ENGINE_COLUMNS if c not in bars.columns]
    if missing_cols:
        raise BarDataError(f"Bars are missing columns: {missing_cols}")
    frame = bars[ENGINE_COLUMNS].copy()
    if pd.api.types.is_datetime64_any_dtype(frame["trading_date"]):
        frame["trading_date"] = frame["trading_date"].dt.date
    frame = frame[frame["fin_code"].isin(config.symbols)]
    frame = frame[(frame["trading_date"] >= config.start) & (frame["trading_date"] <= config.end)]
    frame = frame.reset_index(drop=True)
    if frame.empty:
        raise BarDataError(f"No bars for symbols {list(config.symbols)} between {config.start} and {config.end}")
    absent = sorted(set(config.symbols) - set(frame["fin_code"]))
    if absent:
        raise BarDataError(f"No bars in the date range for symbols {absent}")
    if frame.duplicated(["fin_code", "trading_date"]).any():
        raise BarDataError("Duplicate (fin_code, trading_date) rows in the bars")
    ordered = frame.sort_values(["fin_code", "trading_date"]).reset_index(drop=True)
    if not (ordered["trading_date"].equals(frame["trading_date"]) and ordered["fin_code"].equals(frame["fin_code"])):
        raise BarDataError("Bars must be sorted by (fin_code, trading_date)")
    prices = frame[_PRICE_COLUMNS]
    if prices.isna().any().any() or (prices <= 0).any().any():
        raise BarDataError("Bars contain null or non-positive open/high/low/close")
    frame["fin_code"] = frame["fin_code"].astype("int64")
    return frame


def data_fingerprint(frame: pd.DataFrame) -> str:
    """SHA-256 of the bars the run used (same recipe as the data layer's bars_fingerprint)."""
    row_hashes = pd.util.hash_pandas_object(frame[ENGINE_COLUMNS], index=False).to_numpy().tobytes()
    return hashlib.sha256(row_hashes).hexdigest()


def make_run_id(config: RunConfig, fingerprint: str) -> str:
    return hashlib.sha256((config.snapshot_json() + fingerprint).encode("utf-8")).hexdigest()[:16]


def _bar(row: tuple) -> Bar:
    fin_code, trading_date, open_, high, low, close, volume = row
    return Bar(int(fin_code), trading_date, float(open_), float(high), float(low), float(close),
               None if pd.isna(volume) else float(volume))


def run_backtest(
    config: RunConfig,
    bars: pd.DataFrame,
    *,
    order_manager: OrderManager | None = None,
    risk_manager: RiskManager | None = None,
    executor: ExecutionSimulator | None = None,
    portfolio: Portfolio | None = None,
    sink: EventSink | None = None,
    output_dir: str | Path | None = None,
) -> RunResult:
    """Run one backtest. `bars` is the store's bar table. With `output_dir`, results are written there."""
    frame = prepare_bars(config, bars)
    fingerprint = data_fingerprint(frame)
    run_id = make_run_id(config, fingerprint)

    order_manager = order_manager or NoOpOrderManager()
    risk_manager = risk_manager or AcceptAllRisk()
    executor = executor or NoFillExecution()
    portfolio = portfolio or CashOnlyPortfolio(config.initial_capital)
    log = EventLog()
    sinks: list[EventSink] = [log] + ([sink] if sink is not None else [])

    def emit(event: Event) -> None:
        for s in sinks:
            s.emit(event)

    series = {int(code): SymbolSeries(group) for code, group in frame.groupby("fin_code", sort=True)}
    first_date = {code: s.dates[0] for code, s in series.items()}
    last_date = {code: s.dates[-1] for code, s in series.items()}
    bars_by_date: dict[date, dict[int, Bar]] = {}
    for row in frame.itertuples(index=False, name=None):
        bar = _bar(row)
        bars_by_date.setdefault(bar.trading_date, {})[bar.fin_code] = bar
    calendar = sorted(bars_by_date)

    strategy = create_strategy(config.strategy)
    strategy.on_start(StrategyContext(tuple(sorted(config.symbols))))
    strategy_view = _ReadOnlyPortfolio(portfolio)

    pending: list[Order] = []
    last_close: dict[int, float] = {}
    all_signals: list[Signal] = []
    equity_points: list[EquityPoint] = []
    symbols = set(config.symbols)

    for t in calendar:
        today = bars_by_date[t]

        # 1. Open phase
        if pending:
            due, pending = pending, []
            _open_phase(t, due, today, executor, portfolio, emit)

        # 2. Close phase
        for code in sorted(today):
            b = today[code]
            last_close[code] = b.close
            emit(MarketEvent(t, b.fin_code, b.open, b.high, b.low, b.close, b.volume))
        for code in sorted(symbols - set(today)):
            if first_date[code] < t < last_date[code]:
                emit(DataQualityEvent(t, code, "missing_bar",
                                      f"no bar on {t}; valued at last close {last_close[code]}"))
        point = portfolio.mark_to_market(t, dict(last_close))
        equity_points.append(point)
        emit(PortfolioUpdateEvent(t, point.cash, point.positions_value, point.equity))

        # 3. Strategy
        history = TruncatedHistory(series, t)
        signals: list[Signal] = []
        for code in sorted(today):
            for sig in strategy.on_bar(today[code], history, strategy_view):
                _check_signal(sig, t, symbols)
                signals.append(sig)
                emit(SignalEvent(t, sig.fin_code, sig.action.value, sig.strategy_id, sig.reason))
        all_signals.extend(signals)

        # 4. Orders and risk
        if signals:
            for order in order_manager.create_orders(signals, strategy_view, t):
                if order.created_on != t:
                    raise ExecutionError(f"Order {order.order_id} created_on {order.created_on}, expected {t}")
                if order.fin_code not in today:
                    raise ExecutionError(f"Order {order.order_id} is for symbol {order.fin_code} with no bar on {t}")
                emit(OrderEvent(t, order.order_id, order.fin_code, order.side, order.quantity, OrderStatus.CREATED))
                decision = risk_manager.validate(order, strategy_view, today[order.fin_code])
                if decision.accepted:
                    pending.append(order)
                    emit(OrderEvent(t, order.order_id, order.fin_code, order.side, order.quantity,
                                    OrderStatus.ACCEPTED))
                else:
                    emit(RiskRejectionEvent(t, order.order_id, order.fin_code, decision.reason))
                    emit(OrderEvent(t, order.order_id, order.fin_code, order.side, order.quantity,
                                    OrderStatus.REJECTED, decision.reason))

    equity = pd.DataFrame([vars(p) for p in equity_points],
                          columns=["trading_date", "cash", "positions_value", "equity"])
    result = RunResult(config, run_id, fingerprint, log.events, all_signals, equity, len(calendar))
    if output_dir is not None:
        from algo_engine.engine.output import write_run
        result = RunResult(config, run_id, fingerprint, log.events, all_signals, equity, len(calendar),
                           write_run(Path(output_dir), result))
    return result


def _check_signal(sig: Signal, t: date, symbols: set[int]) -> None:
    if sig.fin_code not in symbols:
        raise SignalError(f"Signal for symbol {sig.fin_code}, which is not in the run")
    if sig.trading_date != t:
        raise SignalError(f"Signal dated {sig.trading_date} returned while processing bar {t}")


def _open_phase(
    t: date,
    due: Sequence[Order],
    today: Mapping[int, Bar],
    executor: ExecutionSimulator,
    portfolio: Portfolio,
    emit,
) -> None:
    for order in due:
        if order.created_on >= t:
            raise ExecutionError(f"Order {order.order_id} created on {order.created_on} offered for execution on {t}")
    by_id = {o.order_id: o for o in due}
    if len(by_id) != len(due):
        raise ExecutionError(f"Duplicate order ids among pending orders on {t}")
    filled: set[int] = set()
    for fill in executor.execute(due, today, t):
        order = by_id.get(fill.order_id)
        if order is None or fill.order_id in filled:
            raise ExecutionError(f"Fill for unknown or already filled order {fill.order_id} on {t}")
        if fill.trading_date != t:
            raise ExecutionError(f"Fill for order {fill.order_id} dated {fill.trading_date}, expected {t}")
        if (fill.fin_code, fill.side, fill.quantity) != (order.fin_code, order.side, order.quantity):
            raise ExecutionError(f"Fill for order {fill.order_id} does not match the order (partial fills are not supported)")
        filled.add(fill.order_id)
        portfolio.apply_fill(fill)
        emit(FillEvent(t, fill.order_id, fill.fin_code, fill.side, fill.quantity, fill.price, fill.costs))
        emit(OrderEvent(t, order.order_id, order.fin_code, order.side, order.quantity, OrderStatus.FILLED))
    for order in due:
        if order.order_id not in filled:
            emit(OrderEvent(t, order.order_id, order.fin_code, order.side, order.quantity,
                            OrderStatus.EXPIRED, "day order not filled"))
