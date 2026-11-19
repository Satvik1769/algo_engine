"""Performance analytics for backtests."""

import numpy as np
import pandas as pd


def calculate_metrics(
    equity_curve: pd.Series | list[float],
    *,
    benchmark: pd.Series | list[float] | None = None,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> dict[str, float]:
    equity = pd.Series(equity_curve, dtype=float).dropna()
    if len(equity) < 2:
        return {
            "total_return": 0.0,
            "cagr": 0.0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
        }

    returns = equity.pct_change().dropna()
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1)
    years = len(equity) / periods_per_year
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) if years > 0 else 0.0
    excess = returns - risk_free_rate / periods_per_year
    sharpe = float(
        excess.mean() / excess.std(ddof=1) * np.sqrt(periods_per_year)
    ) if excess.std(ddof=1) > 0 else 0.0
    downside = returns[returns < 0]
    sortino = float(
        excess.mean() / downside.std(ddof=1) * np.sqrt(periods_per_year)
    ) if len(downside) > 1 and downside.std(ddof=1) > 0 else 0.0
    running_max = equity.cummax()
    max_drawdown = float(((equity - running_max) / running_max).min())
    positive = returns[returns > 0]
    negative = returns[returns < 0]
    win_rate = float(len(positive) / len(returns)) if len(returns) else 0.0
    profit_factor = float(positive.sum() / abs(negative.sum())) if negative.sum() != 0 else (
        float("inf") if len(positive) else 0.0
    )

    metrics = {
        "total_return": total_return,
        "cagr": cagr,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown": max_drawdown,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
    }
    if benchmark is not None:
        bench = pd.Series(benchmark, dtype=float).dropna()
        if len(bench) > 1:
            bench_returns = bench.pct_change().dropna()
            aligned = pd.concat([returns, bench_returns], axis=1).dropna()
            if len(aligned) > 1:
                metrics["benchmark_correlation"] = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
    return metrics


class PerformanceAnalyzer:
    def calculate(self, equity_curve: pd.Series | list[float], **kwargs) -> dict[str, float]:
        return calculate_metrics(equity_curve, **kwargs)

