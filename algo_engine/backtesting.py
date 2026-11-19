"""Event-driven backtesting engine."""

from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from .analytics import calculate_metrics
from .portfolio import Portfolio
from .orders import OrderSide, OrderStatus, OrderType, Trade
from .strategies import Strategy


@dataclass
class BacktestResult:
    portfolio: Portfolio
    equity_curve: pd.Series
    trades: list[Trade]
    metrics: dict[str, float]


class BacktestEngine:
    def __init__(
        self,
        initial_capital: float = 100_000.0,
        commission: float = 0.0,
        slippage: float = 0.0,
        quantity: int = 1,
    ):
        if initial_capital < 0:
            raise ValueError("initial_capital cannot be negative")
        if commission < 0 or slippage < 0:
            raise ValueError("commission and slippage cannot be negative")
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.quantity = quantity

    def run(
        self,
        data: Mapping[str, pd.DataFrame] | pd.DataFrame,
        strategy: Strategy,
        *,
        start_date=None,
        end_date=None,
        benchmark=None,
    ) -> BacktestResult:
        if isinstance(data, pd.DataFrame):
            data = {"DEFAULT": data}
        if not isinstance(strategy, Strategy):
            raise TypeError("strategy must implement the Strategy interface")

        prepared: dict[str, pd.DataFrame] = {}
        all_dates: set[pd.Timestamp] = set()
        for symbol, frame in data.items():
            frame = frame.copy()
            frame.columns = [str(column).lower() for column in frame.columns]
            if not isinstance(frame.index, pd.DatetimeIndex):
                frame.index = pd.to_datetime(frame.index)
            frame = frame.sort_index()
            if start_date is not None:
                frame = frame[frame.index >= pd.Timestamp(start_date)]
            if end_date is not None:
                frame = frame[frame.index <= pd.Timestamp(end_date)]
            if frame.empty:
                continue
            prepared[symbol] = frame
            all_dates.update(frame.index.tolist())

        portfolio = Portfolio(self.initial_capital)
        equity = {}
        for date in sorted(all_dates):
            prices: dict[str, float] = {}
            for symbol, frame in prepared.items():
                available = frame[frame.index <= date]
                if available.empty:
                    continue
                price = float(available.iloc[-1]["close"])
                prices[symbol] = price

                slice_frame = available
                signals = strategy.generate_signals(slice_frame)
                if "signal" not in signals.columns or signals.empty:
                    continue
                signal = int(signals.iloc[-1]["signal"])
                if signal > 0:
                    position = portfolio.positions.get(symbol)
                    if position is not None and position.quantity > 0:
                        continue
                    fill_price = price * (1 + self.slippage)
                    shares = min(
                        self.quantity,
                        int(portfolio.cash // (fill_price * self.quantity)),
                    )
                    total_cost = shares * fill_price + self.commission
                    if shares > 0 and portfolio.cash >= total_cost:
                        trade = Trade(
                            symbol=symbol,
                            side=OrderSide.BUY,
                            quantity=shares,
                            price=fill_price,
                            timestamp=date,
                            commission=self.commission,
                        )
                        portfolio.execute_trade(trade)
                elif signal < 0:
                    position = portfolio.positions.get(symbol)
                    if position is not None and position.quantity > 0:
                        fill_price = price * (1 - self.slippage)
                        trade = Trade(
                            symbol=symbol,
                            side=OrderSide.SELL,
                            quantity=position.quantity,
                            price=fill_price,
                            timestamp=date,
                            commission=self.commission,
                        )
                        portfolio.execute_trade(trade)
            equity[pd.Timestamp(date)] = portfolio.value(prices)

        equity_curve = pd.Series(equity, dtype=float)
        metrics = calculate_metrics(equity_curve)
        if benchmark is not None:
            bench_series = benchmark if isinstance(benchmark, pd.Series) else pd.Series(benchmark)
            metrics = calculate_metrics(equity_curve, benchmark=bench_series)
        return BacktestResult(
            portfolio=portfolio,
            equity_curve=equity_curve,
            trades=portfolio.trades,
            metrics=metrics,
        )


def run_backtest(
    data: Mapping[str, pd.DataFrame] | pd.DataFrame,
    strategy: Strategy,
    initial_capital: float = 100_000.0,
    **kwargs,
) -> BacktestResult:
    engine = BacktestEngine(initial_capital=initial_capital)
    return engine.run(data, strategy, **kwargs)
