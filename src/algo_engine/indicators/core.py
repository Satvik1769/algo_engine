"""Causal indicators (Milestone 5). The value at index t depends only on inputs at indices <= t.

Conventions (pinned by tests):
* Warm-up: values are NaN until enough history exists. Nothing is back-filled.
* EMA: alpha = 2 / (n + 1), seeded with the SMA of the first n values (first value at index n-1).
* Wilder smoothing (RSI, ATR): avg_t = (avg_{t-1} * (n - 1) + x_t) / n, seeded with the simple mean of
  the first n inputs (first value at index n, because the first change / true range needs a prior bar).
* Bollinger: standard deviation is the population one (ddof=0), as in Bollinger's definition.
Inputs are expected to be NaN-free bars from the validated store.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _check_window(n: int) -> None:
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError(f"window must be an integer >= 1, got {n!r}")


class IncrementalEma:
    """EMA fed one value at a time, for strategies that must not re-scan history on every bar."""

    def __init__(self, n: int) -> None:
        _check_window(n)
        self.n = n
        self._alpha = 2.0 / (n + 1)
        self._count = 0
        self._sum = 0.0
        self.value = math.nan

    def update(self, x: float) -> float:
        """Add the next value and return the EMA (NaN during warm-up)."""
        self._count += 1
        if self._count < self.n:
            self._sum += x
        elif self._count == self.n:
            self.value = (self._sum + x) / self.n
        else:
            self.value = self._alpha * x + (1.0 - self._alpha) * self.value
        return self.value


def sma(close: pd.Series, n: int) -> pd.Series:
    """Simple moving average of the last n values (including the current one)."""
    _check_window(n)
    return close.rolling(n, min_periods=n).mean()


def ema(close: pd.Series, n: int) -> pd.Series:
    """Exponential moving average, SMA-seeded. Identical to feeding `IncrementalEma` value by value."""
    state = IncrementalEma(n)
    return pd.Series([state.update(float(x)) for x in close], index=close.index, dtype=float)


def _wilder(values: np.ndarray, n: int, first: int) -> np.ndarray:
    """Wilder smoothing of `values[first:]`; output has NaN before index first + n - 1."""
    out = np.full(len(values), np.nan)
    if len(values) < first + n:
        return out
    out[first + n - 1] = values[first:first + n].mean()
    for i in range(first + n, len(values)):
        out[i] = (out[i - 1] * (n - 1) + values[i]) / n
    return out


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI = 100 - 100 / (1 + avg_gain / avg_loss).

    With no losses the value is 100 (50 when there are no gains either, i.e. a flat window).
    """
    _check_window(n)
    change = close.diff().to_numpy()
    gain = np.where(change > 0, change, 0.0)
    loss = np.where(change < 0, -change, 0.0)
    avg_gain, avg_loss = _wilder(gain, n, first=1), _wilder(loss, n, first=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    out = np.where((avg_loss == 0) & (avg_gain > 0), 100.0, out)
    out = np.where((avg_loss == 0) & (avg_gain == 0), 50.0, out)
    out[np.isnan(avg_gain)] = np.nan
    return pd.Series(out, index=close.index, dtype=float)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder average true range. TR = max(H-L, |H-prev C|, |L-prev C|), defined from the second bar on."""
    _check_window(n)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1, skipna=False)
    return pd.Series(_wilder(tr.to_numpy(dtype=float), n, first=1), index=close.index, dtype=float)


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    """Columns `mid` (SMA), `upper` = mid + k * std, `lower` = mid - k * std (population std)."""
    _check_window(n)
    if k <= 0:
        raise ValueError(f"k must be > 0, got {k!r}")
    mid = sma(close, n)
    std = close.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"mid": mid, "upper": mid + k * std, "lower": mid - k * std})


def rolling_returns(close: pd.Series, n: int) -> pd.Series:
    """Simple return over the last n bars: close_t / close_{t-n} - 1. Uses only past values."""
    _check_window(n)
    return close / close.shift(n) - 1.0
