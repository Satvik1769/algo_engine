"""Robustness statistics: deflated Sharpe ratio, trade-order Monte Carlo, cost/slippage stress configs.

Deflated Sharpe follows Bailey and Lopez de Prado (2014). It is the probability that the true Sharpe ratio of
the selected strategy is above zero after allowing for the best-of-N selection and for non-normal returns.
All Sharpe values inside the formula are per-period (daily), not annualized.
"""
from __future__ import annotations

import math
from dataclasses import fields, replace
from statistics import NormalDist

import numpy as np

from algo_engine.engine.config import RunConfig
from algo_engine.research.grid import period_sharpe

EULER_GAMMA = 0.5772156649015329
_N = NormalDist()


def expected_max_sharpe(trial_sharpes: np.ndarray, n_trials: int) -> float:
    """Expected maximum per-period Sharpe of `n_trials` unskilled trials with the observed cross-trial spread."""
    sd = float(np.std(trial_sharpes, ddof=1))
    return sd * ((1 - EULER_GAMMA) * _N.inv_cdf(1 - 1 / n_trials)
                 + EULER_GAMMA * _N.inv_cdf(1 - 1 / (n_trials * math.e)))


def deflated_sharpe(returns: np.ndarray, trial_sharpes: list[float], rf: float = 0.0) -> dict:
    """Deflated Sharpe of the selected strategy's daily `returns` given the per-period Sharpes of all trials.

    Returns a dict with `dsr` (probability, or None when undefined) and the inputs; `reason` explains a None.
    """
    r = np.asarray(returns, dtype=float)
    sharpes = np.array([s for s in trial_sharpes if s is not None and not math.isnan(s)], dtype=float)
    out: dict = {"n_trials": int(len(trial_sharpes)), "observations": int(len(r)), "dsr": None}
    if len(sharpes) < 2:
        out["reason"] = "needs at least two trials with a defined Sharpe"
        return out
    sr = period_sharpe(r, rf)
    if math.isnan(sr) or len(r) < 3:
        out["reason"] = "selected strategy's Sharpe is undefined"
        return out
    if float(np.std(sharpes, ddof=1)) < 1e-12:
        out["reason"] = "all trials have the same Sharpe"
        return out
    z = (r - r.mean()) / r.std()
    skew, kurt = float(np.mean(z ** 3)), float(np.mean(z ** 4))
    sr0 = expected_max_sharpe(sharpes, len(sharpes))
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if denom <= 0:
        out["reason"] = "non-normality term is not positive"
        return out
    stat = (sr - sr0) * math.sqrt(len(r) - 1) / math.sqrt(denom)
    out.update(dsr=_N.cdf(stat), sharpe_period=sr, sharpe_annualized=sr * math.sqrt(252), skew=skew,
               kurtosis=kurt, benchmark_sharpe_period=sr0, benchmark_sharpe_annualized=sr0 * math.sqrt(252))
    return out


def trade_monte_carlo(net_pnl: np.ndarray, initial_capital: float, n: int = 2000, seed: int = 0) -> dict | None:
    """Reorder and resample the closed trades' rupee P&L.

    Shuffle (same trades, random order): final equity cannot change, only the path, so the max drawdown of the
    shuffles is compared with the actual one. Bootstrap (with replacement): distribution of total P&L.
    Trades are treated as sequential and independent; overlapping positions are ignored.
    """
    pnl = np.asarray(net_pnl, dtype=float)
    if len(pnl) < 2:
        return None
    rng = np.random.default_rng(seed)

    def max_dd(paths: np.ndarray) -> np.ndarray:
        eq = initial_capital + np.cumsum(paths, axis=1)
        eq = np.concatenate([np.full((len(eq), 1), initial_capital), eq], axis=1)
        peak = np.maximum.accumulate(eq, axis=1)
        return (eq / peak - 1.0).min(axis=1)

    actual = float(max_dd(pnl[None, :])[0])
    shuffled = max_dd(rng.permuted(np.tile(pnl, (n, 1)), axis=1))
    boot_total = pnl[rng.integers(0, len(pnl), size=(n, len(pnl)))].sum(axis=1)
    q = lambda a: {f"p{p}": float(np.percentile(a, p)) for p in (5, 50, 95)}  # noqa: E731
    return {
        "trades": int(len(pnl)), "resamples": n, "seed": seed, "actual_max_drawdown": actual,
        "shuffled_max_drawdown": q(shuffled),
        "share_shuffles_deeper_than_actual": float(np.mean(shuffled < actual)),
        "bootstrap_total_pnl": q(boot_total),
        "share_bootstrap_total_pnl_positive": float(np.mean(boot_total > 0)),
    }


def scale_costs(config: RunConfig, factor: float) -> RunConfig:
    """Multiply every charge in every cost schedule by `factor` (GST is a tax rate on fees, so it stays)."""
    scaled = []
    for sched in config.costs:
        changes = {}
        for f in fields(sched):
            if f.name in ("brokerage_rate", "brokerage_max_per_order", "stt_buy_rate", "stt_sell_rate",
                          "exchange_txn_rate", "sebi_rate", "stamp_buy_rate", "dp_charge_per_sell"):
                v = getattr(sched, f.name)
                if v is not None:
                    changes[f.name] = v * factor
        scaled.append(replace(sched, **changes))
    return replace(config, costs=tuple(scaled))
