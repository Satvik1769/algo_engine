"""M8: metrics against hand-computed values, edge cases, benchmark relations, bootstrap, and a run directory."""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from algo_engine.analytics import (
    bootstrap_ci, drawdown_stats, equity_metrics, exposure_metrics, relative_metrics, sharpe_ratio, trade_metrics,
)
from algo_engine.analytics.report import analyze, format_summary, load_run, write_metrics
from algo_engine.engine import RunConfig, run_backtest

D0 = date(2024, 1, 1)


def series(values, start=D0, step=1) -> pd.Series:
    return pd.Series([float(v) for v in values], index=[start + timedelta(days=step * i) for i in range(len(values))])


def test_return_metrics_by_hand():
    eq = series([100, 101, 99.99, 101.9898])            # returns +1%, -1%, +2%
    m = equity_metrics(eq, rf=0.0)
    assert m["total_return"] == pytest.approx(0.019898)
    assert m["cagr"] == pytest.approx(1.019898 ** (365.25 / 3) - 1)
    r = [0.01, -0.01, 0.02]
    mean = sum(r) / 3
    sd = math.sqrt(sum((x - mean) ** 2 for x in r) / 2)
    assert m["sharpe"] == pytest.approx(mean / sd * math.sqrt(252))
    assert m["annualized_volatility"] == pytest.approx(sd * math.sqrt(252))
    downside = math.sqrt((0.01 ** 2) / 3)
    assert m["sortino"] == pytest.approx(mean / downside * math.sqrt(252))
    assert m["max_drawdown"] == pytest.approx(-0.01)
    assert m["calmar"] == pytest.approx(m["cagr"] / 0.01)


def test_risk_free_rate_lowers_sharpe_by_the_formula():
    r = np.array([0.01, -0.01, 0.02, 0.005])
    rf = 0.065
    excess = r - rf / 252
    expected = excess.mean() / excess.std(ddof=1) * math.sqrt(252)
    assert sharpe_ratio(r, rf) == pytest.approx(expected)
    assert sharpe_ratio(r, rf) < sharpe_ratio(r, 0.0)


def test_drawdown_depth_dates_and_time_under_water():
    eq = series([100, 120, 90, 100, 130, 117, 125])
    dd = drawdown_stats(eq)
    assert dd["max_drawdown"] == pytest.approx(-0.25)
    assert dd["max_drawdown_peak_date"] == D0 + timedelta(days=1)
    assert dd["max_drawdown_trough_date"] == D0 + timedelta(days=2)
    assert dd["max_drawdown_recovery_date"] == D0 + timedelta(days=4) and dd["max_drawdown_recovered"]
    assert dd["longest_underwater_trading_days"] == 3 and dd["longest_underwater_calendar_days"] == 3
    assert dd["currently_underwater"] is True            # 130 -> 125 is still under the last peak


def test_unrecovered_drawdown_counts_until_the_last_date():
    dd = drawdown_stats(series([100, 110, 80, 85]))
    assert dd["max_drawdown_recovered"] is False and dd["max_drawdown_recovery_date"] is None
    assert dd["longest_underwater_trading_days"] == 2


def test_edge_cases_flat_short_and_tiny_series():
    flat = equity_metrics(series([100] * 10))
    assert flat["total_return"] == 0 and flat["max_drawdown"] == 0 and flat["annualized_volatility"] == 0
    assert math.isnan(flat["sharpe"]) and math.isnan(flat["sortino"]) and math.isnan(flat["calmar"])
    one = equity_metrics(series([100]))
    assert one["total_return"] == 0 and math.isnan(one["sharpe"])
    two = equity_metrics(series([100, 110]))
    assert two["total_return"] == pytest.approx(0.1) and math.isnan(two["sharpe"])      # one return only
    wiped = equity_metrics(series([100, 50, 0.0001]))
    assert wiped["max_drawdown"] < -0.99
    assert math.isnan(sharpe_ratio(np.array([0.01] * 5)))                               # constant returns


def test_exposure():
    frame = pd.DataFrame({"equity": [100.0, 100.0, 200.0, 200.0], "positions_value": [0.0, 50.0, 100.0, 0.0]})
    m = exposure_metrics(frame)
    assert m["mean_exposure"] == pytest.approx((0 + 0.5 + 0.5 + 0) / 4)
    assert m["pct_days_invested"] == 0.5


def trades_frame(pnls, entry_price=100.0, quantity=10, holding=5):
    n = len(pnls)
    return pd.DataFrame({
        "fin_code": 1, "entry_date": [str(D0 + timedelta(days=10 * i)) for i in range(n)],
        "exit_date": [str(D0 + timedelta(days=10 * i + holding)) for i in range(n)], "quantity": quantity,
        "entry_price": entry_price, "exit_price": entry_price, "gross_pnl": pnls, "costs": 0.0, "dividends": 0.0,
        "net_pnl": pnls, "holding_days": holding})


def test_trade_metrics_by_hand():
    t = trade_metrics(trades_frame([100.0, -50.0, -50.0, 200.0]))
    assert t["closed_trades"] == 4 and t["win_rate"] == 0.5
    assert t["profit_factor"] == pytest.approx(300 / 100)
    assert t["average_win"] == 150 and t["average_loss"] == -50 and t["expectancy"] == 50
    assert t["best_trade"] == 200 and t["worst_trade"] == -50 and t["total_net_pnl"] == 200
    assert t["mean_trade_return"] == pytest.approx(50 / 1000)          # 200 / 4 trades over 1000 bought
    assert t["average_holding_calendar_days"] == 5


def test_trade_metrics_edge_cases():
    assert trade_metrics(trades_frame([10.0, 20.0]))["profit_factor"] == math.inf       # no losses
    assert math.isnan(trade_metrics(trades_frame([0.0]))["profit_factor"])               # no profit, no loss
    none = trade_metrics(trades_frame([]))
    assert none["closed_trades"] == 0 and math.isnan(none["win_rate"]) and none["total_net_pnl"] == 0
    only_losses = trade_metrics(trades_frame([-5.0, -5.0]))
    assert only_losses["profit_factor"] == 0 and only_losses["win_rate"] == 0


def test_holding_period_in_trading_days_uses_the_calendar():
    cal = [D0 + timedelta(days=i) for i in range(40) if (D0 + timedelta(days=i)).weekday() < 5]
    t = trades_frame([1.0])
    t["entry_date"], t["exit_date"] = str(date(2024, 1, 3)), str(date(2024, 1, 10))   # Wed -> Wed
    assert trade_metrics(t, cal)["average_holding_trading_days"] == 5


def test_relative_metrics_recover_known_beta_and_alpha():
    rng = np.random.default_rng(1)
    rb = rng.normal(0.0005, 0.01, 400)
    rs = 1.5 * rb + 0.0004
    bench = series(100 * np.cumprod(np.r_[1.0, 1 + rb]))
    strat = series(100 * np.cumprod(np.r_[1.0, 1 + rs]))
    rel = relative_metrics(strat, bench, rf=0.0)
    assert rel["beta"] == pytest.approx(1.5, abs=1e-9)
    assert rel["alpha_annualized"] == pytest.approx(0.0004 * 252, abs=1e-9)
    assert rel["correlation"] == pytest.approx(1.0)
    assert rel["tracking_error"] == pytest.approx(np.std(rs - rb, ddof=1) * math.sqrt(252))
    assert rel["information_ratio"] == pytest.approx(np.mean(rs - rb) * 252 / (np.std(rs - rb, ddof=1) * math.sqrt(252)))
    ident = relative_metrics(bench, bench)
    assert ident["beta"] == pytest.approx(1.0) and math.isnan(ident["information_ratio"])   # zero tracking error
    assert ident["excess_total_return"] == 0


def test_relative_metrics_use_common_dates_only_and_short_input_is_nan():
    bench = series(range(100, 120))
    strat = series(range(100, 110))[::2]
    assert relative_metrics(strat, bench)["common_observations"] == 5
    assert math.isnan(relative_metrics(series([1, 2]), series([1, 2]))["beta"])


def test_bootstrap_is_seeded_and_brackets_the_estimate():
    rng = np.random.default_rng(7)
    values = rng.normal(0.01, 0.05, 200)
    a = bootstrap_ci(values, lambda s: s.mean(axis=1), 1000, seed=3)
    b = bootstrap_ci(values, lambda s: s.mean(axis=1), 1000, seed=3)
    assert a == b and a["low"] < values.mean() < a["high"]
    assert a["high"] - a["low"] == pytest.approx(2 * 1.96 * 0.05 / math.sqrt(200), rel=0.25)
    assert bootstrap_ci([1.0], lambda s: s.mean(axis=1)) is None
    const = bootstrap_ci([2.0, 2.0, 2.0], lambda s: s.mean(axis=1), 100)
    assert const["low"] == const["high"] == 2.0


def swing_run(tmp_path: Path, name="a"):
    closes = [round(100 + 4 * math.sin(i / 3), 2) for i in range(60)]
    dates = pd.bdate_range("2024-01-01", periods=len(closes)).date
    bars = pd.DataFrame({"fin_code": 1, "trading_date": dates, "open": [c - 0.2 for c in closes],
                         "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
                         "close": closes, "volume": 1000.0})
    cfg = RunConfig.from_dict({"symbols": [1], "start": "2024-01-01", "end": "2024-12-31",
                               "strategy": {"name": "ema_crossover", "params": {"fast": 2, "slow": 3}}})
    res = run_backtest(cfg, bars, output_dir=tmp_path / name)
    return res, bars


def test_analyze_a_run_directory(tmp_path: Path):
    res, bars = swing_run(tmp_path)
    run = load_run(res.output_dir)
    assert run.fills == sum(1 for e in res.events if type(e).__name__ == "FillEvent")
    bench = pd.Series(bars["close"].to_numpy(), index=list(bars["trading_date"]))
    m = analyze(run, bench, rf=0.065, n_boot=300)
    eq = res.equity["equity"]
    assert m["strategy"]["total_return"] == pytest.approx(eq.iloc[-1] / eq.iloc[0] - 1)
    assert m["strategy"]["max_drawdown"] == pytest.approx((eq / eq.cummax() - 1).min())
    assert m["trades"]["closed_trades"] == len(res.trades) and m["trades"]["total_fills"] == run.fills
    assert m["costs"]["total_costs"] == pytest.approx(sum(
        e.costs for e in res.events if type(e).__name__ == "FillEvent"))
    assert m["benchmark"]["label"] == "Nifty 50 (price return)" and m["relative"]["common_observations"] == len(eq)
    assert any("closed trades" in w for w in m["warnings"]) and any("price index" in w for w in m["warnings"])
    text = json.dumps(m, allow_nan=False)                         # no NaN/inf leaks into the JSON
    assert "NaN" not in text
    assert "Sharpe" in format_summary(m)
    again = analyze(load_run(res.output_dir), bench, rf=0.065, n_boot=300)
    assert json.dumps(again, sort_keys=True) == json.dumps(m, sort_keys=True)     # deterministic
    path = write_metrics(res.output_dir, m)
    assert json.loads(path.read_text())["meta"]["run_id"] == res.run_id


def test_analyze_without_trades_or_benchmark(tmp_path: Path):
    res, _ = swing_run(tmp_path)
    run = load_run(res.output_dir)
    empty = run.__class__(**{**run.__dict__, "trades": run.trades.iloc[0:0], "fills": 0})
    m = analyze(empty, None, n_boot=50)
    assert m["trades"]["closed_trades"] == 0 and m["trades"]["win_rate"] is None and "benchmark" not in m
    assert "mean_trade_return" not in m["confidence"]
    flat = run.__class__(**{**run.__dict__, "trades": run.trades.iloc[0:0],
                            "equity": run.equity.assign(equity=1_000_000.0, positions_value=0.0)})
    mf = analyze(flat, None, n_boot=50)
    assert mf["strategy"]["sharpe"] is None and any("undefined" in w for w in mf["warnings"])
