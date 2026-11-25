"""Performance metrics (docs/ARCHITECTURE.md section 8). Pure functions on pandas/numpy, no I/O.

Conventions
  r_t = E_t / E_(t-1) - 1 on the daily equity series, N = 252 periods per year, `rf` is an annual rate.
  Total return = E_T / E_0 - 1.  CAGR = (E_T / E_0) ** (365.25 / calendar_days) - 1.
  Sharpe  = mean(r - rf/N) / std(r, ddof=1) * sqrt(N).
  Sortino = mean(r - rf/N) / DD * sqrt(N), DD = sqrt(mean(min(r - rf/N, 0) ** 2)) over all periods.
  Max drawdown = min_t (E_t / max_(s<=t) E_s - 1).
  Win rate and profit factor use each round trip's net P&L (after costs, with dividends).
Undefined results (zero volatility, no losing trades, fewer than two points) are NaN, except a profit factor with
profits and no losses, which is +inf. `float("nan")` and `inf` are written as null and "inf" in metrics.json.
Bootstrap intervals are percentile intervals from an i.i.d. resample, seeded for repeatability. For daily returns
the i.i.d. assumption ignores autocorrelation, so treat the Sharpe interval as indicative.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from datetime import date

import numpy as np
import pandas as pd

TRADING_DAYS = 252
_EPS = 1e-12
LOW_SAMPLE_TRADES = 30


def daily_returns(equity: pd.Series) -> pd.Series:
    return equity.astype(float).pct_change().dropna()


def sharpe_ratio(returns, rf: float = 0.0, periods: int = TRADING_DAYS, axis: int = -1):
    """Annualized Sharpe. Works on a 1-D array (float result) or a 2-D array along `axis` (array of results)."""
    r = np.asarray(returns, dtype=float)
    n = r.shape[axis]
    if n < 2:
        return math.nan if r.ndim == 1 else np.full(np.delete(r.shape, axis % r.ndim), math.nan)
    excess = r - rf / periods
    sd = excess.std(axis=axis, ddof=1)
    mean = excess.mean(axis=axis)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(sd < _EPS, np.nan, mean / sd * math.sqrt(periods))
    return float(out) if out.ndim == 0 else out


def sortino_ratio(returns, rf: float = 0.0, periods: int = TRADING_DAYS) -> float:
    r = np.asarray(returns, dtype=float)
    if len(r) < 2:
        return math.nan
    excess = r - rf / periods
    downside = math.sqrt(float(np.mean(np.minimum(excess, 0.0) ** 2)))
    if downside < _EPS:
        return math.nan
    return float(excess.mean() / downside * math.sqrt(periods))


def drawdown_stats(equity: pd.Series) -> dict[str, object]:
    """Deepest drawdown with its peak, trough and recovery dates, and the longest time under water."""
    values = equity.to_numpy(dtype=float)
    dates = list(equity.index)
    n = len(values)
    out: dict[str, object] = {
        "max_drawdown": 0.0, "max_drawdown_peak_date": None, "max_drawdown_trough_date": None,
        "max_drawdown_recovery_date": None, "max_drawdown_recovered": True,
        "longest_underwater_trading_days": 0, "longest_underwater_calendar_days": 0, "currently_underwater": False,
    }
    if n == 0:
        return out
    run_max, peak_i = values[0], 0
    span_min, span_trough = values[0], 0
    underwater = False
    worst = 0.0
    longest = (0, 0)  # (trading days, calendar days)

    def close_span(end_i: int, recovered: bool) -> None:
        nonlocal worst, longest
        depth = span_min / run_max - 1.0
        span = (end_i - peak_i, (dates[end_i] - dates[peak_i]).days)
        if span > longest:
            longest = span
        if depth < worst:
            worst = depth
            out.update(max_drawdown=depth, max_drawdown_peak_date=dates[peak_i],
                       max_drawdown_trough_date=dates[span_trough],
                       max_drawdown_recovery_date=dates[end_i] if recovered else None,
                       max_drawdown_recovered=recovered)

    for i, v in enumerate(values):
        if v >= run_max:
            if underwater:
                close_span(i, True)
                underwater = False
            run_max, peak_i = v, i
            span_min, span_trough = v, i
        else:
            underwater = True
            if v < span_min:
                span_min, span_trough = v, i
    if underwater:
        close_span(n - 1, False)
    out["currently_underwater"] = underwater
    out["longest_underwater_trading_days"], out["longest_underwater_calendar_days"] = longest
    return out


def equity_metrics(equity: pd.Series, rf: float = 0.0, periods: int = TRADING_DAYS) -> dict[str, object]:
    """Return and risk metrics for an equity (or price) series indexed by date."""
    equity = equity.astype(float)
    out: dict[str, object] = {"observations": int(len(equity))}
    if len(equity) < 2:
        out.update(total_return=0.0, cagr=math.nan, annualized_volatility=math.nan, sharpe=math.nan,
                   sortino=math.nan, calmar=math.nan, **drawdown_stats(equity))
        return out
    first, last = float(equity.iloc[0]), float(equity.iloc[-1])
    days = (equity.index[-1] - equity.index[0]).days
    total = last / first - 1.0
    if days <= 0:
        cagr = math.nan
    elif last <= 0:
        cagr = -1.0
    else:
        cagr = (last / first) ** (365.25 / days) - 1.0
    r = daily_returns(equity).to_numpy()
    vol = float(r.std(ddof=1) * math.sqrt(periods)) if len(r) >= 2 else math.nan
    dd = drawdown_stats(equity)
    calmar = cagr / abs(dd["max_drawdown"]) if dd["max_drawdown"] and not math.isnan(cagr) else math.nan
    out.update(start_date=equity.index[0], end_date=equity.index[-1], calendar_days=days, start_value=first,
               end_value=last, total_return=total, cagr=cagr, annualized_volatility=vol,
               sharpe=sharpe_ratio(r, rf, periods), sortino=sortino_ratio(r, rf, periods), calmar=calmar, **dd)
    return out


def exposure_metrics(equity_frame: pd.DataFrame) -> dict[str, float]:
    """Time-weighted mean of positions_value / equity, and the share of days with any position."""
    if equity_frame.empty:
        return {"mean_exposure": math.nan, "pct_days_invested": math.nan}
    eq = equity_frame["equity"].astype(float)
    pos = equity_frame["positions_value"].astype(float)
    ratio = (pos / eq).where(eq > 0, 0.0)
    return {"mean_exposure": float(ratio.mean()), "pct_days_invested": float((pos > 0).mean())}


def relative_metrics(strategy: pd.Series, benchmark: pd.Series, rf: float = 0.0,
                     periods: int = TRADING_DAYS) -> dict[str, object]:
    """Strategy against a benchmark on their common dates: beta, annualized alpha, tracking error, information ratio."""
    common = strategy.index.intersection(benchmark.index)
    s, b = strategy.loc[common].astype(float), benchmark.loc[common].astype(float)
    out: dict[str, object] = {"common_observations": int(len(common))}
    if len(common) < 3:
        out.update(beta=math.nan, alpha_annualized=math.nan, correlation=math.nan, tracking_error=math.nan,
                   information_ratio=math.nan, excess_total_return=math.nan)
        return out
    rs, rb = daily_returns(s).to_numpy(), daily_returns(b).to_numpy()
    xs, xb = rs - rf / periods, rb - rf / periods
    var_b = float(np.var(xb, ddof=1))
    if var_b < _EPS ** 2:
        beta = alpha = corr = math.nan
    else:
        beta = float(np.cov(xs, xb, ddof=1)[0, 1] / var_b)
        alpha = float((xs.mean() - beta * xb.mean()) * periods)
        sd_s = float(np.std(xs, ddof=1))
        corr = float(np.corrcoef(xs, xb)[0, 1]) if sd_s > _EPS else math.nan
    active = rs - rb
    te = float(active.std(ddof=1) * math.sqrt(periods))
    ir = float(active.mean() * periods / te) if te > _EPS else math.nan
    out.update(beta=beta, alpha_annualized=alpha, correlation=corr, tracking_error=te, information_ratio=ir,
               excess_total_return=float(s.iloc[-1] / s.iloc[0] - b.iloc[-1] / b.iloc[0]))
    return out


def trade_metrics(trades: pd.DataFrame, calendar: list[date] | None = None) -> dict[str, object]:
    """Statistics of closed round trips (net of costs, with dividends). `calendar` gives trading-day holding periods."""
    n = len(trades)
    out: dict[str, object] = {"closed_trades": n}
    if n == 0:
        out.update(win_rate=math.nan, profit_factor=math.nan, average_win=math.nan, average_loss=math.nan,
                   expectancy=math.nan, mean_trade_return=math.nan, best_trade=math.nan, worst_trade=math.nan,
                   average_holding_calendar_days=math.nan, average_holding_trading_days=math.nan,
                   total_net_pnl=0.0)
        return out
    pnl = trades["net_pnl"].to_numpy(dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    profit, loss = float(wins.sum()), float(-losses.sum())
    if loss > 0:
        pf = profit / loss
    else:
        pf = math.inf if profit > 0 else math.nan
    out.update(
        win_rate=float(len(wins) / n), profit_factor=pf,
        average_win=float(wins.mean()) if len(wins) else math.nan,
        average_loss=float(losses.mean()) if len(losses) else math.nan,
        expectancy=float(pnl.mean()), mean_trade_return=float(trade_returns(trades).mean()),
        best_trade=float(pnl.max()), worst_trade=float(pnl.min()), total_net_pnl=float(pnl.sum()),
        average_holding_calendar_days=float(trades["holding_days"].mean()),
        average_holding_trading_days=math.nan)
    if calendar:
        days = np.array([_as_date(d) for d in calendar], dtype="datetime64[D]")
        entry = np.array([_as_date(d) for d in trades["entry_date"]], dtype="datetime64[D]")
        exit_ = np.array([_as_date(d) for d in trades["exit_date"]], dtype="datetime64[D]")
        held = np.searchsorted(days, exit_, side="right") - np.searchsorted(days, entry, side="right")
        out["average_holding_trading_days"] = float(held.mean())
    return out


def trade_returns(trades: pd.DataFrame) -> np.ndarray:
    """Net P&L of each round trip divided by the value bought (entry price times shares bought)."""
    cost = trades["entry_price"].to_numpy(dtype=float) * trades["quantity"].to_numpy(dtype=float)
    return trades["net_pnl"].to_numpy(dtype=float) / cost


def _as_date(value) -> date:
    return value if isinstance(value, date) and not isinstance(value, pd.Timestamp) else pd.Timestamp(value).date()


def bootstrap_ci(values, statistic: Callable[[np.ndarray], np.ndarray], n_boot: int = 2000, seed: int = 0,
                 level: float = 0.95) -> dict[str, float] | None:
    """Percentile interval of `statistic` over i.i.d. resamples of `values`.

    `statistic` receives a 2-D array (one resample per row) and returns one value per row. Returns None when
    there are fewer than two values.
    """
    v = np.asarray(values, dtype=float)
    if len(v) < 2:
        return None
    rng = np.random.default_rng(seed)
    samples = v[rng.integers(0, len(v), size=(n_boot, len(v)))]
    stats = np.asarray(statistic(samples), dtype=float)
    stats = stats[~np.isnan(stats)]
    if len(stats) == 0:
        return None
    tail = (1.0 - level) / 2.0 * 100.0
    low, high = np.percentile(stats, [tail, 100.0 - tail])
    return {"low": float(low), "high": float(high), "level": level, "resamples": int(len(stats))}
