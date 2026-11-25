"""M9: the static report is built from the run directory, always carries the caveats, and escapes its inputs."""
from __future__ import annotations

import json
import math
import re
import xml.dom.minidom
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from algo_engine.analytics.report import analyze, load_run, write_benchmark, write_metrics
from algo_engine.engine import RunConfig, run_backtest
from algo_engine.reports import build_report, caveats
from algo_engine.reports.static import _nice_ticks, line_chart


def make_run(tmp_path: Path, name="a", benchmark=True):
    closes = [round(100 + 4 * math.sin(i / 3), 2) for i in range(60)]
    dates = pd.bdate_range("2024-01-01", periods=len(closes)).date
    bars = pd.DataFrame({"fin_code": 1, "trading_date": dates, "open": [c - 0.2 for c in closes],
                         "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
                         "close": closes, "volume": 1000.0})
    cfg = RunConfig.from_dict({"symbols": [1], "start": "2024-01-01", "end": "2024-12-31",
                               "strategy": {"name": "ema_crossover", "params": {"fast": 2, "slow": 3}}})
    res = run_backtest(cfg, bars, output_dir=tmp_path / name)
    run = load_run(res.output_dir)
    bench = pd.Series(bars["close"].to_numpy() * 2, index=list(bars["trading_date"])) if benchmark else None
    write_metrics(res.output_dir, analyze(run, bench, n_boot=100))
    if bench is not None:
        write_benchmark(res.output_dir, bench, list(run.equity["trading_date"]))
    return res


def test_report_contains_the_required_sections_and_valid_svg(tmp_path: Path):
    res = make_run(tmp_path)
    page = build_report(res.output_dir).read_text()
    for needle in ("Read this first", "survivorship-biased", "In-sample", "Costs:", "Dividends:", "Benchmark:",
                   "Nifty 50 (price return)", "<h2>Performance</h2>", "<h2>Equity</h2>", "<h2>Drawdown</h2>",
                   "<h2>Trades</h2>", "Bar data fingerprint", res.data_fingerprint, res.run_id, "Configuration"):
        assert needle in page, needle
    svgs = re.findall(r"<svg.*?</svg>", page, re.S)
    assert len(svgs) == 2
    for svg in svgs:
        xml.dom.minidom.parseString(svg)
    assert page.count("<tr><td>") > 20 and "NaN" not in page


def test_report_is_rebuilt_identically_from_the_run_directory(tmp_path: Path):
    res = make_run(tmp_path)
    first = build_report(res.output_dir).read_bytes()
    assert build_report(res.output_dir).read_bytes() == first
    other = make_run(tmp_path, "b")
    assert build_report(other.output_dir).read_bytes() == first


def test_report_without_benchmark_says_so_and_still_builds(tmp_path: Path):
    res = make_run(tmp_path, benchmark=False)
    page = build_report(res.output_dir).read_text()
    assert "No benchmark comparison is included" in page and "rebased" not in page


def test_report_needs_metrics_json(tmp_path: Path):
    res = run_backtest(RunConfig.from_dict({"symbols": [1], "start": "2024-01-01", "end": "2024-12-31",
                                            "strategy": {"name": "noop"}}),
                       pd.DataFrame({"fin_code": 1, "trading_date": [date(2024, 1, 2), date(2024, 1, 3)],
                                     "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}),
                       output_dir=tmp_path / "x")
    with pytest.raises(FileNotFoundError, match="analytics.run"):
        build_report(res.output_dir)


def test_values_from_files_are_escaped(tmp_path: Path):
    res = make_run(tmp_path)
    cfg = json.loads((res.output_dir / "config.json").read_text())
    cfg["strategy"]["name"] = "<script>alert(1)</script>"
    (res.output_dir / "config.json").write_text(json.dumps(cfg))
    page = build_report(res.output_dir).read_text()
    assert "<script>" not in page and "&lt;script&gt;" in page


def test_caveats_react_to_the_run():
    metrics = {"warnings": ["extra warning"]}
    base = {"costs": [{"dp_charge_per_sell": 20.0}], "slippage_bps": 5.0, "symbols": [100325]}
    text = " ".join(caveats(base, {}, metrics))
    assert "survivorship" in text and "in-sample" in text.lower() and "Rs 20.00" in text and "5.0 bps" in text
    assert "extra warning" in text and "ZERO costs" not in text and "traded instrument is an index" not in text
    assert "ZERO costs" in " ".join(caveats(base, {"zero_cost_diagnostic": True}, metrics))
    assert "traded instrument is an index" in " ".join(caveats({**base, "symbols": [100256265]}, {}, metrics))


def test_chart_helpers():
    assert _nice_ticks(0.0, 10.0) == [0.0, 5.0, 10.0]
    ticks = _nice_ticks(-38.4, 0.0)
    assert all(-38.4 <= t <= 0.0 for t in ticks) and len(ticks) >= 3
    assert "not enough data" in line_chart("t", [("a", [date(2024, 1, 1)], [1.0], "#000")], str)
    flat = line_chart("flat", [("a", [date(2024, 1, 1), date(2024, 1, 2)], [5.0, 5.0], "#000")], str)
    xml.dom.minidom.parseString(flat)
