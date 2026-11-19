"""Trading strategy interface and initial strategy implementations."""

from abc import ABC, abstractmethod

import numpy as np
import pandas as pd


class Strategy(ABC):
    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """Return OHLCV data with a ``signal`` column of -1, 0, or 1."""

    def calculate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        return self.generate_signals(data)


class EMACrossoverStrategy(Strategy):
    def __init__(self, fast_period: int = 20, slow_period: int = 50):
        if fast_period <= 0 or slow_period <= 0:
            raise ValueError("EMA periods must be positive")
        if fast_period >= slow_period:
            raise ValueError("fast_period must be smaller than slow_period")
        self.fast_period = fast_period
        self.slow_period = slow_period

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        frame = data.copy()
        fast = frame["close"].ewm(span=self.fast_period, adjust=False).mean()
        slow = frame["close"].ewm(span=self.slow_period, adjust=False).mean()
        frame["signal"] = np.where(fast > slow, 1, np.where(fast < slow, -1, 0))
        frame["signal"] = pd.Series(frame["signal"], index=frame.index).diff().clip(-1, 1).fillna(frame["signal"])
        return frame


class RSIMeanReversionStrategy(Strategy):
    def __init__(self, period: int = 14, oversold: float = 30, overbought: float = 70):
        if period <= 0:
            raise ValueError("period must be positive")
        if not 0 <= oversold < overbought <= 100:
            raise ValueError("RSI thresholds must satisfy 0 <= oversold < overbought <= 100")
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        frame = data.copy()
        delta = frame["close"].diff()
        gain = delta.clip(lower=0).rolling(self.period).mean()
        loss = (-delta.clip(upper=0)).rolling(self.period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        frame["rsi"] = rsi
        frame["signal"] = np.select([rsi < self.oversold, rsi > self.overbought], [1, -1], default=0)
        return frame


class BollingerBandsStrategy(Strategy):
    def __init__(self, period: int = 20, std_multiplier: float = 2.0):
        if period <= 0:
            raise ValueError("period must be positive")
        if std_multiplier <= 0:
            raise ValueError("std_multiplier must be positive")
        self.period = period
        self.std_multiplier = std_multiplier

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        frame = data.copy()
        middle = frame["close"].rolling(self.period).mean()
        std = frame["close"].rolling(self.period).std()
        upper = middle + self.std_multiplier * std
        lower = middle - self.std_multiplier * std
        frame["signal"] = np.select(
            [frame["close"] < lower, frame["close"] > upper],
            [1, -1],
            default=0,
        )
        return frame

