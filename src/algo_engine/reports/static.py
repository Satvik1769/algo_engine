"""Static HTML report for one finished run (Milestone 9, minimal).

Built only from files in the run directory (config.json, manifest.json, equity.csv, trades.csv, metrics.json and,
when present, benchmark.csv), so a report can be rebuilt at any time and is byte-identical for the same files.
Charts are inline SVG, so the file is self-contained and needs no extra dependency. Every value that comes from
a file is HTML-escaped. The caveats section is always printed.
"""
from __future__ import annotations

import html
import json
import math
from datetime import date
from pathlib import Path

import pandas as pd

MAX_TRADE_ROWS = 500
_STRATEGY_COLOR, _BENCH_COLOR = "#1f5fbf", "#d9822b"

_CSS = """
body{font-family:-apple-system,Helvetica,Arial,sans-serif;margin:2rem auto;max-width:1000px;color:#222;line-height:1.4}
h1{margin-bottom:.2rem}h2{margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.2rem}
table{border-collapse:collapse;margin:.5rem 0;font-size:.9rem}th,td{padding:.25rem .7rem;border-bottom:1px solid #eee;text-align:right}
th:first-child,td:first-child{text-align:left}th{background:#f6f6f6}
.caveats{background:#fff7e6;border:1px solid #f0c36d;padding:.5rem 1.5rem;border-radius:4px}
.muted{color:#777;font-size:.85rem}pre{background:#f6f6f6;padding:.7rem;overflow-x:auto;font-size:.8rem}
.neg{color:#b3261e}svg text{font-family:Helvetica,Arial,sans-serif;font-size:11px;fill:#555}
"""


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _num(x, digits=2) -> str:
    return "n/a" if x is None else (_e(x) if isinstance(x, str) else f"{x:,.{digits}f}")


def _pct(x, digits=2) -> str:
    return "n/a" if x is None else f"{x * 100:,.{digits}f}%"


def _money(x) -> str:
    return "n/a" if x is None else f"{x:,.0f}"


# ---- charts ----

def _nice_ticks(lo: float, hi: float, target: int = 5) -> list[float]:
    """Round tick values (1, 2 or 5 times a power of ten) inside [lo, hi]."""
    raw = (hi - lo) / max(target - 1, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    first = math.ceil(lo / step - 1e-9)
    return [k * step for k in range(first, math.floor(hi / step + 1e-9) + 1)]


def line_chart(title: str, series: list[tuple[str, list[date], list[float], str]], y_fmt, *,
               fill_first: bool = False, cap_top_at_zero: bool = False, width: int = 900, height: int = 280) -> str:
    """SVG line chart. `series` items are (label, dates, values, color). Dates are placed on a calendar-day axis."""
    left, right, top, bottom = 72, 16, 26, 28
    usable = [s for s in series if len(s[1]) >= 2]
    head = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{_e(title)}">'
    if not usable:
        return f'{head}<text x="10" y="20">{_e(title)}: not enough data</text></svg>'
    d0 = min(s[1][0] for s in usable)
    d1 = max(s[1][-1] for s in usable)
    span = max((d1 - d0).days, 1)
    ys = [v for s in usable for v in s[2]]
    lo, hi = min(ys), max(ys)
    if hi - lo < 1e-12:
        lo, hi = lo - 1.0, hi + 1.0
    pad = (hi - lo) * 0.05
    lo, hi = lo - pad, (min(hi, 0.0) if cap_top_at_zero else hi + pad)

    def px(d: date) -> float:
        return left + (d - d0).days / span * (width - left - right)

    def py(v: float) -> float:
        return top + (hi - v) / (hi - lo) * (height - top - bottom)

    out = [head, f'<text x="{left}" y="14" style="font-weight:bold">{_e(title)}</text>']
    for tick in _nice_ticks(lo, hi):
        y = py(tick)
        out.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="#e5e5e5"/>')
        out.append(f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">{_e(y_fmt(tick))}</text>')
    for i in range(6):
        d = d0 + (d1 - d0) * i / 5
        x = px(d)
        anchor = "start" if i == 0 else "end" if i == 5 else "middle"
        out.append(f'<text x="{x:.1f}" y="{height - 8}" text-anchor="{anchor}">{d.isoformat()}</text>')
    for idx, (label, dates, values, color) in enumerate(usable):
        pts = " ".join(f"{px(d):.1f},{py(v):.1f}" for d, v in zip(dates, values))
        if fill_first and idx == 0:
            base = py(0.0 if lo <= 0.0 <= hi else lo)
            out.append(f'<polygon points="{px(dates[0]):.1f},{base:.1f} {pts} {px(dates[-1]):.1f},{base:.1f}" '
                       f'fill="{color}" fill-opacity="0.18" stroke="none"/>')
        out.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5"/>')
        lx = left + 230 + idx * 250          # legend on the title row, outside the plot area
        out.append(f'<line x1="{lx}" y1="10" x2="{lx + 16}" y2="10" stroke="{color}" stroke-width="2"/>')
        out.append(f'<text x="{lx + 22}" y="14">{_e(label)}</text>')
    out.append("</svg>")
    return "".join(out)


def _drawdown(values: pd.Series) -> list[float]:
    return list(values / values.cummax() - 1.0)


# ---- caveats ----

def caveats(config: dict, manifest: dict, metrics: dict) -> list[str]:
    """Caveats that always apply, plus the ones this run triggers, then the metric warnings."""
    items = [
        "Universe: today's constituents only (current-constituents universe, survivorship-biased). Companies that "
        "left the index are not tested, which flatters stock results.",
        "In-sample: every figure comes from one backtest over the whole period, with no out-of-sample or walk-forward "
        "test. The bootstrap intervals show estimation noise only, not the risk of an overfitted strategy.",
    ]
    costs = config.get("costs") or [{}]
    dp = costs[0].get("dp_charge_per_sell")
    items.append(
        "Costs: current Zerodha delivery rates are applied to the whole period although the rates changed over time; "
        f"slippage is {config.get('slippage_bps')} bps per fill; the DP charge on sells is a placeholder of "
        f"Rs {_num(dp)} per sell. No volume cap, partial fills, circuit limits or T+1 settlement are modelled.")
    items.append(
        "Dividends: taken from the corporate-actions table, which has none before 2022-08 and is probably "
        "incomplete after 2024. They are credited on the ex-date to shares held at the previous close, with no tax or "
        "TDS. Results before 2022-08 omit dividend income.")
    items.append(
        "Prices are adjusted for splits and bonuses by reconciling each event with the price jump at its ex-date. "
        "Events that could not be decided, unexplained jumps, rejected rows and missing days stay as they are in the "
        "data (see the data fingerprint and the load report).")
    if "benchmark" in metrics:
        items.append(f"Benchmark: {metrics['benchmark']['label']}. A price index has no dividends, so it understates "
                     "a total-return benchmark by roughly the dividend yield.")
    else:
        items.append("No benchmark comparison is included in this report. Do not read these results without one.")
    if manifest.get("zero_cost_diagnostic"):
        items.append("This run has ZERO costs. It is a diagnostic, not a result.")
    index_codes = {100256265, 100256266}
    symbols = set(config.get("symbols") or [])
    if symbols and symbols <= index_codes:
        items.append("The traded instrument is an index. It cannot be bought directly, so the run checks the strategy "
                     "mechanics and is not a tradable result.")
    items.extend(metrics.get("warnings") or [])
    return items


# ---- sections ----

def _metric_rows(m: dict) -> str:
    s, b, r, t = m["strategy"], m.get("benchmark"), m.get("relative"), m["trades"]
    rows = [("Total return", s["total_return"], b and b["total_return"], _pct),
            ("CAGR", s["cagr"], b and b["cagr"], _pct),
            ("Annualized volatility", s["annualized_volatility"], b and b["annualized_volatility"], _pct),
            ("Sharpe", s["sharpe"], b and b["sharpe"], _num),
            ("Sortino", s["sortino"], b and b["sortino"], _num),
            ("Calmar", s["calmar"], b and b["calmar"], _num),
            ("Max drawdown", s["max_drawdown"], b and b["max_drawdown"], _pct),
            ("Longest time under water (trading days)", s["longest_underwater_trading_days"],
             b and b["longest_underwater_trading_days"], lambda x: _num(x, 0))]
    body = "".join(f"<tr><td>{_e(name)}</td><td>{fmt(a)}</td><td>{'' if bench is None and b is None else fmt(bench)}</td></tr>"
                   for name, a, bench, fmt in rows)
    head = f"<tr><th></th><th>Strategy</th><th>{_e(b['label']) if b else 'Benchmark'}</th></tr>"
    extra = [("Mean exposure", _pct(s.get("mean_exposure"))), ("Days invested", _pct(s.get("pct_days_invested")))]
    if r:
        extra += [("Excess total return", _pct(r["excess_total_return"])), ("Beta", _num(r["beta"])),
                  ("Alpha (annualized)", _pct(r["alpha_annualized"])), ("Correlation", _num(r["correlation"])),
                  ("Tracking error", _pct(r["tracking_error"])), ("Information ratio", _num(r["information_ratio"]))]
    extra_html = "".join(f"<tr><td>{_e(k)}</td><td>{v}</td></tr>" for k, v in extra)
    return f"<table>{head}{body}</table><table>{extra_html}</table>"


def _trade_stats(m: dict) -> str:
    t, c = m["trades"], m["costs"]
    rows = [("Closed round trips", _num(t["closed_trades"], 0)), ("Fills", _num(t.get("total_fills"), 0)),
            ("Win rate", _pct(t["win_rate"])), ("Profit factor", _num(t["profit_factor"])),
            ("Average win", _money(t["average_win"])), ("Average loss", _money(t["average_loss"])),
            ("Expectancy per trade", _money(t["expectancy"])), ("Mean trade return", _pct(t["mean_trade_return"])),
            ("Best / worst trade", f"{_money(t['best_trade'])} / {_money(t['worst_trade'])}"),
            ("Average holding (calendar / trading days)",
             f"{_num(t['average_holding_calendar_days'], 1)} / {_num(t['average_holding_trading_days'], 1)}"),
            ("Total net P&L of closed trades", _money(t["total_net_pnl"])),
            ("Total costs paid", _money(c["total_costs"])), ("Dividends received", _money(c["total_dividends"]))]
    ci = m.get("confidence", {})
    for key, label, fmt in (("sharpe", "Sharpe", _num), ("mean_trade_return", "Mean trade return", _pct),
                            ("win_rate", "Win rate", _pct)):
        if ci.get(key):
            v = ci[key]
            rows.append((f"{label}, {v['level']:.0%} bootstrap interval", f"[{fmt(v['low'])}, {fmt(v['high'])}]"))
    return "<table>" + "".join(f"<tr><td>{_e(k)}</td><td>{v}</td></tr>" for k, v in rows) + "</table>"


def _trade_table(trades: pd.DataFrame) -> str:
    if trades.empty:
        return "<p>No closed trades.</p>"
    cols = ["fin_code", "entry_date", "exit_date", "quantity", "entry_price", "exit_price", "gross_pnl", "costs",
            "dividends", "net_pnl", "holding_days"]
    head = "".join(f"<th>{_e(c)}</th>" for c in cols)
    rows = []
    for rec in trades.head(MAX_TRADE_ROWS)[cols].itertuples(index=False):
        cells = []
        for col, v in zip(cols, rec):
            if col in ("entry_price", "exit_price", "gross_pnl", "costs", "dividends", "net_pnl"):
                cls = ' class="neg"' if col in ("gross_pnl", "net_pnl") and v < 0 else ""
                cells.append(f"<td{cls}>{v:,.2f}</td>")
            else:
                cells.append(f"<td>{_e(v)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    note = ""
    if len(trades) > MAX_TRADE_ROWS:
        note = f'<p class="muted">Showing the first {MAX_TRADE_ROWS} of {len(trades)} trades; see trades.csv.</p>'
    return f"<table><tr>{head}</tr>{''.join(rows)}</table>{note}"


def build_report(run_dir: str | Path) -> Path:
    """Write `report.html` into `run_dir` and return its path. `metrics.json` must exist (analytics.run makes it)."""
    d = Path(run_dir)
    if not (d / "metrics.json").exists():
        raise FileNotFoundError(f"{d / 'metrics.json'} not found; run `python -m algo_engine.analytics.run --run-dir {d}` first")
    config = json.loads((d / "config.json").read_text())
    manifest = json.loads((d / "manifest.json").read_text())
    metrics = json.loads((d / "metrics.json").read_text())
    equity = pd.read_csv(d / "equity.csv")
    trades = pd.read_csv(d / "trades.csv")
    dates = [date.fromisoformat(x) for x in equity["trading_date"]]
    eq = pd.Series(equity["equity"].to_numpy(dtype=float), index=dates)

    eq_series = [("Strategy equity", dates, list(eq), _STRATEGY_COLOR)]
    dd_series = [("Strategy drawdown", dates, _drawdown(eq), _STRATEGY_COLOR)]
    bench_file = d / "benchmark.csv"
    if "benchmark" in metrics and bench_file.exists():
        b = pd.read_csv(bench_file)
        bs = pd.Series(b["close"].to_numpy(dtype=float), index=[date.fromisoformat(x) for x in b["trading_date"]])
        bs = bs.loc[bs.index.intersection(eq.index)]
        if len(bs) >= 2:
            label = metrics["benchmark"]["label"]
            eq_series.append((f"{label}, rebased", list(bs.index), list(bs / bs.iloc[0] * eq.iloc[0]), _BENCH_COLOR))
            dd_series.append((label, list(bs.index), _drawdown(bs), _BENCH_COLOR))

    meta = metrics["meta"]
    strategy = config.get("strategy", {})
    params = ", ".join(f"{k}={v}" for k, v in (strategy.get("params") or {}).items())
    parts = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
        f"<title>Backtest report {_e(meta['run_id'])}</title><style>{_CSS}</style></head><body>",
        f"<h1>Backtest report</h1><p class='muted'>Run {_e(meta['run_id'])}</p>",
        '<div class="caveats"><strong>Read this first</strong><ul>'
        + "".join(f"<li>{_e(c)}</li>" for c in caveats(config, manifest, metrics)) + "</ul></div>",
        "<h2>Setup</h2><table>"
        f"<tr><td>Strategy</td><td>{_e(strategy.get('name'))} ({_e(params)})</td></tr>"
        f"<tr><td>Symbols (fin_code)</td><td>{_e(', '.join(str(s) for s in config.get('symbols', [])))}</td></tr>"
        f"<tr><td>Period</td><td>{_e(manifest.get('first_date'))} to {_e(manifest.get('last_date'))} "
        f"({_e(manifest.get('calendar_days'))} trading days)</td></tr>"
        f"<tr><td>Initial capital</td><td>{_money(config.get('initial_capital'))}</td></tr>"
        f"<tr><td>Risk-free rate (for Sharpe and Sortino)</td><td>{_pct(meta.get('risk_free_rate'))}</td></tr>"
        f"<tr><td>Price basis</td><td>{_e(config.get('price_basis'))}</td></tr></table>",
        "<h2>Performance</h2>", _metric_rows(metrics),
        "<h2>Equity</h2>", line_chart("Equity (INR)", eq_series, lambda v: f"{v:,.0f}"),
        "<h2>Drawdown</h2>", line_chart("Drawdown from peak", dd_series, lambda v: f"{v * 100:.0f}%", fill_first=True,
                                                  cap_top_at_zero=True),
        "<h2>Trades</h2>", _trade_stats(metrics), _trade_table(trades),
        "<h2>Reproducibility</h2><table>"
        f"<tr><td>Run id</td><td>{_e(meta['run_id'])}</td></tr>"
        f"<tr><td>Bar data fingerprint</td><td>{_e(manifest.get('data_fingerprint'))}</td></tr>"
        f"<tr><td>Dividends fingerprint</td><td>{_e(manifest.get('dividends_fingerprint') or 'none')}</td></tr>"
        f"<tr><td>Seed</td><td>{_e(manifest.get('seed'))}</td></tr>"
        f"<tr><td>Bootstrap</td><td>{_e(meta.get('bootstrap_resamples'))} resamples, seed {_e(meta.get('bootstrap_seed'))}, "
        f"{_e(meta.get('bootstrap_method'))}</td></tr></table>",
        "<h2>Configuration</h2>", f"<pre>{_e(json.dumps(config, indent=2, sort_keys=True))}</pre>",
        "</body></html>",
    ]
    path = d / "report.html"
    path.write_text("\n".join(parts) + "\n", encoding="utf-8", newline="\n")
    return path
