"""M7: average-cost portfolio accounting, reconciliation, and the default end-to-end run."""
from __future__ import annotations

import math
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from algo_engine.engine import ExecutionError, FillEvent, RunConfig, Side, run_backtest
from algo_engine.engine.ports import Fill
from algo_engine.portfolio import AverageCostPortfolio, Trade

D = date(2024, 1, 1)


def swing_bars() -> pd.DataFrame:
    closes = [round(100 + 4 * math.sin(i / 3), 2) for i in range(48)]
    dates = pd.bdate_range("2024-01-01", periods=len(closes)).date
    return pd.DataFrame({"fin_code": 1, "trading_date": dates, "open": [c - 0.2 for c in closes],
                         "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
                         "close": closes, "volume": 1000.0})


def cfg_swing() -> RunConfig:
    return RunConfig.from_dict({"symbols": [1], "start": "2024-01-01", "end": "2024-12-31",
                                "strategy": {"name": "ema_crossover", "params": {"fast": 2, "slow": 3}}})


def fill(side, qty, price, costs=0.0, day=D, code=1, order_id=1) -> Fill:
    return Fill(order_id, code, side, qty, price, day, costs)


def test_buy_then_full_sell_hand_calculation():
    pf = AverageCostPortfolio(100_000)
    pf.apply_fill(fill(Side.BUY, 100, 50.0, costs=10.0))
    assert pf.cash() == pytest.approx(100_000 - 5_000 - 10)
    assert pf.position_qty(1) == 100 and pf.open_positions() == 1 and pf.average_price(1) == 50.0
    point = pf.mark_to_market(D, {1: 52.0})
    assert point.positions_value == 5_200 and point.equity == pytest.approx(100_000 - 10 + 200)
    pf.apply_fill(fill(Side.SELL, 100, 55.0, costs=12.0, day=D + timedelta(days=10)))
    assert pf.cash() == pytest.approx(100_000 - 5_010 + 5_500 - 12)
    assert pf.open_positions() == 0 and pf.position_qty(1) == 0
    (t,) = pf.trades
    assert t == Trade(1, D, D + timedelta(days=10), 100, 50.0, 55.0, gross_pnl=500.0, costs=22.0,
                      net_pnl=478.0, holding_days=10)
    assert pf.total_costs == 22.0
    assert pf.cash() - 100_000 == pytest.approx(t.net_pnl)


def test_partial_close_keeps_average_price_and_one_trade():
    pf = AverageCostPortfolio(100_000)
    pf.apply_fill(fill(Side.BUY, 100, 50.0, 5.0))
    pf.apply_fill(fill(Side.SELL, 40, 60.0, 3.0, day=D + timedelta(days=1)))
    assert pf.position_qty(1) == 60 and pf.average_price(1) == 50.0 and pf.trades == []
    pf.apply_fill(fill(Side.SELL, 60, 45.0, 2.0, day=D + timedelta(days=2)))
    (t,) = pf.trades
    assert t.quantity == 100 and t.entry_price == 50.0
    assert t.exit_price == pytest.approx((40 * 60 + 60 * 45) / 100)
    assert t.gross_pnl == pytest.approx(40 * 10 + 60 * -5) and t.costs == 10.0
    assert t.net_pnl == pytest.approx(100 - 10)


def test_adding_to_a_position_uses_weighted_average_and_reentry_starts_a_new_trade():
    pf = AverageCostPortfolio(100_000)
    pf.apply_fill(fill(Side.BUY, 100, 10.0))
    pf.apply_fill(fill(Side.BUY, 100, 20.0, day=D + timedelta(days=1)))
    assert pf.average_price(1) == 15.0
    pf.apply_fill(fill(Side.SELL, 200, 15.0, day=D + timedelta(days=2)))
    pf.apply_fill(fill(Side.BUY, 10, 30.0, day=D + timedelta(days=5)))
    pf.apply_fill(fill(Side.SELL, 10, 33.0, day=D + timedelta(days=6)))
    first, second = pf.trades
    assert first.gross_pnl == 0.0 and first.entry_date == D
    assert second.entry_date == D + timedelta(days=5) and second.gross_pnl == pytest.approx(30.0)


def test_invalid_fills_raise_and_leave_state_unchanged():
    pf = AverageCostPortfolio(1_000)
    with pytest.raises(ExecutionError, match="overdraw"):
        pf.apply_fill(fill(Side.BUY, 100, 50.0))
    with pytest.raises(ExecutionError, match="exceeds position"):
        pf.apply_fill(fill(Side.SELL, 1, 50.0))
    with pytest.raises(ExecutionError, match="invalid fill"):
        pf.apply_fill(fill(Side.BUY, 1, 0.0))
    assert pf.cash() == 1_000 and pf.open_positions() == 0 and pf.total_costs == 0
    pf.apply_fill(fill(Side.BUY, 10, 50.0))
    with pytest.raises(ExecutionError, match="No valid close"):
        pf.mark_to_market(D, {})
    with pytest.raises(ValueError):
        AverageCostPortfolio(0)


def test_random_fill_sequences_keep_the_invariants():
    for seed in range(25):
        rng = random.Random(seed)
        initial = 500_000.0
        pf = AverageCostPortfolio(initial)
        day = D
        prices = {c: rng.uniform(20, 500) for c in (1, 2, 3)}
        for step in range(200):
            day += timedelta(days=1)
            code = rng.choice((1, 2, 3))
            prices[code] *= rng.uniform(0.95, 1.05)
            held = pf.position_qty(code)
            costs = rng.uniform(0, 30)
            if held and rng.random() < 0.5:
                pf.apply_fill(fill(Side.SELL, rng.randint(1, held), prices[code], costs, day, code, step))
            else:
                qty = int(min(pf.cash() - costs, 50_000) // prices[code])
                if qty >= 1:
                    pf.apply_fill(fill(Side.BUY, qty, prices[code], costs, day, code, step))
            point = pf.mark_to_market(day, prices)
            assert pf.cash() >= -1e-6 and all(pf.position_qty(c) >= 0 for c in prices)
            assert point.equity == pytest.approx(point.cash + sum(pf.position_qty(c) * prices[c] for c in prices))
            realized = sum(t.net_pnl for t in pf.trades)
            assert point.equity - initial == pytest.approx(realized + pf.open_pnl() - pf.open_costs(), abs=1e-6)
        assert pf.open_positions() == sum(1 for c in prices if pf.position_qty(c) > 0)


def test_default_run_trades_and_reconciles(tmp_path: Path):
    cfg = cfg_swing()
    bars = swing_bars()
    res = run_backtest(cfg, bars, output_dir=tmp_path / "a")
    fills = [e for e in res.events if isinstance(e, FillEvent)]
    assert fills and res.trades
    # final equity = initial capital + cash flows of all fills + value of shares still held
    assert res.equity["equity"].iloc[-1] == pytest.approx(cfg.initial_capital + _fill_pnl(fills, bars))
    assert sum(t.costs for t in res.trades) <= sum(f.costs for f in fills) + 1e-9
    assert (res.equity["cash"] >= 0).all()
    first_buy = next(f for f in fills if f.side is Side.BUY)
    first_signal = min(e.trading_date for e in res.events if type(e).__name__ == "SignalEvent")
    assert first_buy.trading_date > first_signal
    csv = pd.read_csv(res.output_dir / "trades.csv")
    assert len(csv) == len(res.trades) and list(csv.columns)[:3] == ["fin_code", "entry_date", "exit_date"]
    again = run_backtest(cfg, bars.copy(), output_dir=tmp_path / "b")
    for name in ("events.jsonl", "equity.csv", "trades.csv", "manifest.json"):
        assert (res.output_dir / name).read_bytes() == (again.output_dir / name).read_bytes()


def _fill_pnl(fills, bars) -> float:
    """Cash flows of all fills plus the value of the shares still held at the last close."""
    flow = sum((f.quantity * f.price + f.costs) * -1 if f.side is Side.BUY else f.quantity * f.price - f.costs
               for f in fills)
    held = sum(f.quantity * (1 if f.side is Side.BUY else -1) for f in fills)
    return flow + held * bars["close"].iloc[-1]
