"""Core package for the algorithmic trading backtesting engine."""

from .analytics import PerformanceAnalyzer, calculate_metrics
from .backtesting import BacktestEngine, BacktestResult
from .data import HistoricalDataLoader, clean_historical_data
from .orders import Order, OrderSide, OrderStatus, OrderType, Trade
from .portfolio import Portfolio, Position
from .strategies import (
    BollingerBandsStrategy,
    EMACrossoverStrategy,
    RSIMeanReversionStrategy,
    Strategy,
)

__all__ = [
    "HistoricalDataLoader",
    "clean_historical_data",
    "Strategy",
    "EMACrossoverStrategy",
    "RSIMeanReversionStrategy",
    "BollingerBandsStrategy",
    "Order",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "Trade",
    "Position",
    "Portfolio",
    "BacktestEngine",
    "BacktestResult",
    "PerformanceAnalyzer",
    "calculate_metrics",
]

