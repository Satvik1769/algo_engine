"""M10: grid expansion, splits, deflated Sharpe, trade Monte Carlo, stress scaling, experiment runner."""
from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from algo_engine.config.settings import ConfigError
from algo_engine.engine import RunConfig
from algo_engine.research import expand_grid, make_config, run_experiment, train_test_split, walk_forward
from algo_engine.research.grid import period_sharpe, select_best, run_grid
from algo_engine.research.heatmap import heatmap_svg
from algo_engine.research.robustness import deflated_sharpe, scale_costs, trade_monte_carlo


def synthetic_bars(n: int = 900, seed: int = 3, fin_code: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)) + 0.15 * np.sin(t / 25))
    dates = pd.bdate_range("2020-01-01", periods=n).date
    return pd.DataFrame({"fin_code": fin_code, "trading_date": dates, "open": close, "high": close * 1.005,
                         "low": close * 0.995, "close": close, "volume": 1000.0})


def base_config(bars: pd.DataFrame, **kw) -> RunConfig:
    return RunConfig.from_dict({
        "symbols": [1], "start": bars["trading_date"].iloc[0], "end": bars["trading_date"].iloc[-1],
        "strategy": {"name": "ema_crossover", "params": {"fast": 5, "slow": 20}}, **kw})


GRID = {"strategy.params.fast": [3, 5, 8], "strategy.params.slow": [10, 20, 40]}


def test_expand_grid_is_the_product_in_given_order():
    combos = expand_grid({"a.b": [1, 2], "c": ["x", "y", "z"]})
    assert len(combos) == 6 and combos[0] == {"a.b": 1, "c": "x"} and combos[-1] == {"a.b": 2, "c": "z"}
    for bad in ({}, {"a": []}, {"a": 3}):
        with pytest.raises(ConfigError):
            expand_grid(bad)


def test_make_config_applies_overrides_and_rejects_invalid_ones():
    bars = synthetic_bars()
    cfg = make_config(base_config(bars), {"strategy.params.fast": 8, "slippage_bps": 12})
    assert cfg.strategy["params"] == {"fast": 8, "slow": 20} and cfg.slippage_bps == 12
    with pytest.raises(Exception):  # fast >= slow
        make_config(base_config(bars), {"strategy.params.fast": 30})
    with pytest.raises(ConfigError):
        make_config(base_config(bars), {"nonsense_key": 1})


def test_invalid_combinations_are_reported_not_counted():
    bars = synthetic_bars()
    trials, invalid = run_grid(base_config(bars), {"strategy.params.fast": [5, 50], "strategy.params.slow": [20]},
                               bars, None, bars["trading_date"].iloc[0], bars["trading_date"].iloc[-1], 0.0)
    assert len(trials) == 1 and len(invalid) == 1 and invalid[0]["overrides"]["strategy.params.fast"] == 50


def test_train_test_split_and_walk_forward_windows():
    f = train_test_split(date(2020, 1, 1), date(2022, 12, 31), date(2022, 1, 1))
    assert f.train == (date(2020, 1, 1), date(2021, 12, 31)) and f.test == (date(2022, 1, 1), date(2022, 12, 31))
    with pytest.raises(ConfigError):
        train_test_split(date(2020, 1, 1), date(2022, 12, 31), date(2020, 1, 1))
    folds = walk_forward(date(2020, 1, 1), date(2022, 12, 31), 12, 6)
    assert [(x.train, x.test) for x in folds] == [
        ((date(2020, 1, 1), date(2020, 12, 31)), (date(2021, 1, 1), date(2021, 6, 30))),
        ((date(2020, 7, 1), date(2021, 6, 30)), (date(2021, 7, 1), date(2021, 12, 31))),
        ((date(2021, 1, 1), date(2021, 12, 31)), (date(2022, 1, 1), date(2022, 6, 30))),
        ((date(2021, 7, 1), date(2022, 6, 30)), (date(2022, 7, 1), date(2022, 12, 31)))]
    with pytest.raises(ConfigError):
        walk_forward(date(2020, 1, 1), date(2020, 12, 31), 12, 6)


def test_select_best_ignores_nan_and_prefers_first_on_ties():
    class T:
        def __init__(self, s): self.metrics = {"sharpe": s}
    assert select_best([T(float("nan")), T(1.0), T(1.0), T(0.5)]) == 1
    assert select_best([T(float("nan")), T(float("nan"))]) == 0


def test_deflated_sharpe_behaviour():
    rng = np.random.default_rng(0)
    good = rng.normal(0.002, 0.01, 1000)          # per-period Sharpe about 0.2
    spread = list(np.random.default_rng(1).normal(0.0, 0.03, 20))
    many = deflated_sharpe(good, spread)
    assert 0.0 <= many["dsr"] <= 1.0 and many["dsr"] > 0.9
    assert many["benchmark_sharpe_period"] > 0
    zero = rng.normal(0.0, 0.01, 1000)
    assert deflated_sharpe(zero, spread)["dsr"] < 0.5
    # more trials with the same spread raise the bar and lower the score
    few = deflated_sharpe(good, spread[:3])["dsr"]
    assert few > many["dsr"]
    assert deflated_sharpe(good, [0.1])["dsr"] is None and "reason" in deflated_sharpe(good, [0.1])
    assert deflated_sharpe(good, [0.1, 0.1])["dsr"] is None  # no spread across trials


def test_trade_monte_carlo():
    pnl = np.array([5000.0, -2000.0, 3000.0, -4000.0, 6000.0, -1000.0])
    a, b = trade_monte_carlo(pnl, 100_000, 500, 7), trade_monte_carlo(pnl, 100_000, 500, 7)
    assert a == b                                              # seeded
    assert a["shuffled_max_drawdown"]["p5"] <= a["shuffled_max_drawdown"]["p50"] <= a["shuffled_max_drawdown"]["p95"] <= 0
    # hand check of the actual-order drawdown: path 100k,105k,103k,106k,102k,108k,107k -> trough 102k vs peak 106k
    assert a["actual_max_drawdown"] == pytest.approx(102_000 / 106_000 - 1)
    assert a["share_bootstrap_total_pnl_positive"] > 0.5
    assert trade_monte_carlo(np.array([1.0]), 100.0) is None


def test_scale_costs():
    bars = synthetic_bars(60)
    cfg = base_config(bars)
    sched = cfg.costs[0]
    double = scale_costs(cfg, 2.0).costs[0]
    assert double.stt_buy_rate == pytest.approx(2 * sched.stt_buy_rate)
    assert double.dp_charge_per_sell == pytest.approx(2 * sched.dp_charge_per_sell)
    assert double.gst_rate == sched.gst_rate
    assert scale_costs(cfg, 0.0).costs[0].is_zero_cost


def test_heatmap_escapes_labels_and_handles_missing_cells():
    svg = heatmap_svg("<b>t</b>", "x&y", "y", [1, 2], [3, 4], {(1, 3): 1.0, (2, 3): -1.0, (1, 4): float("nan")})
    assert "&lt;b&gt;" in svg and "x&amp;y" in svg and "<b>" not in svg and svg.count("<rect") == 4


def test_period_sharpe_undefined_cases():
    assert np.isnan(period_sharpe(np.array([0.01])))
    assert np.isnan(period_sharpe(np.array([0.01, 0.01, 0.01])))


def test_split_experiment_selects_on_train_only(tmp_path):
    bars = synthetic_bars()
    base = base_config(bars)
    test_start = bars["trading_date"].iloc[600]
    s1 = run_experiment(base, GRID, bars, test_start=test_start, n_mc=100, out_dir=tmp_path)
    assert s1["mode"] == "split" and s1["folds"][0]["valid_trials"] == 9
    sel1 = s1["folds"][0]["selected"]
    # change only the test window's prices: the selection must not move
    changed = bars.copy()
    mask = changed["trading_date"] >= test_start
    changed.loc[mask, ["open", "high", "low", "close"]] *= np.linspace(1.0, 0.3, int(mask.sum()))[:, None]
    s2 = run_experiment(base, GRID, changed, test_start=test_start, n_mc=100)
    assert s2["folds"][0]["selected"] == sel1
    assert s2["folds"][0]["test_metrics"] != s1["folds"][0]["test_metrics"]
    out = tmp_path / s1["experiment_id"]
    assert {p.name for p in out.iterdir()} >= {"summary.json", "registry.csv", "heatmap_train_fold0.svg",
                                                "heatmap_test.svg"}
    reg = pd.read_csv(out / "registry.csv")
    assert set(reg["phase"]) == {"train", "test"} and reg.groupby("phase")["selected"].sum().to_dict() == {"test": 1, "train": 1}
    assert len(s1["stress"]) == 16 and s1["stress"][0]["cost_multiplier"] == 0.0


def test_experiment_output_is_deterministic(tmp_path):
    bars = synthetic_bars()
    base = base_config(bars)
    kw = dict(test_start=bars["trading_date"].iloc[600], n_mc=100)
    a = run_experiment(base, GRID, bars, out_dir=tmp_path / "a", **kw)
    b = run_experiment(base, GRID, bars, out_dir=tmp_path / "b", **kw)
    assert a["experiment_id"] == b["experiment_id"]
    for name in ("summary.json", "registry.csv"):
        ra = (tmp_path / "a" / a["experiment_id"] / name).read_text().replace(str(tmp_path / "a"), "")
        rb = (tmp_path / "b" / b["experiment_id"] / name).read_text().replace(str(tmp_path / "b"), "")
        assert ra == rb


def test_in_sample_is_labelled_and_walk_forward_pools_out_of_sample():
    bars = synthetic_bars()
    base = base_config(bars)
    ins = run_experiment(base, GRID, bars, n_mc=50)
    assert ins["mode"] == "in_sample" and ins["warnings"][0].startswith("IN-SAMPLE ONLY")
    assert "test_metrics" not in ins["folds"][0]
    wf = run_experiment(base, {"strategy.params.fast": [3, 5], "strategy.params.slow": [20]}, bars,
                        walk=(18, 6), n_mc=50)
    assert wf["mode"] == "walk_forward" and len(wf["folds"]) >= 2
    assert all("test_metrics" in f for f in wf["folds"])
    assert wf["pooled_out_of_sample"]["days"] > 0
    json.dumps(wf)  # JSON-safe
    with pytest.raises(ConfigError):
        run_experiment(base, GRID, bars, test_start=date(2021, 1, 1), walk=(12, 6))
