import pandas as pd
import pytest

from algo_engine import (
    BacktestEngine,
    EMACrossoverStrategy,
    HistoricalDataLoader,
    Portfolio,
)
from algo_engine.orders import OrderSide, Trade
from algo_engine.strategies import RSIMeanReversionStrategy


def close_frame(prices):
    return pd.DataFrame(
        {
            "open": prices,
            "high": [price * 1.01 for price in prices],
            "low": [price * 0.99 for price in prices],
            "close": prices,
            "volume": [1000] * len(prices),
        },
        index=pd.date_range("2024-01-01", periods=len(prices)),
    )


def test_load_csv_cleans_duplicates_and_dates(tmp_path):
    csv = tmp_path / "prices.csv"
    pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-01", "2024-01-02"],
            "open": [11, 10, 12],
            "high": [12, 11, 13],
            "low": [10, 9, 11],
            "close": [11, 10, 12],
            "volume": [100, 100, 100],
        }
    ).to_csv(csv, index=False)

    frame = HistoricalDataLoader().load_csv(csv, symbol="TEST")
    assert len(frame) == 2
    assert frame.index.is_monotonic_increasing
    assert frame.attrs["symbol"] == "TEST"


def test_invalid_ohlcv_data_is_rejected():
    frame = close_frame([10, 11])
    frame.iloc[1, frame.columns.get_loc("low")] = 20
    from algo_engine.data import clean_historical_data

    cleaned = clean_historical_data(frame)
    assert len(cleaned) == 1


def test_ema_and_rsi_strategy_output_contains_valid_signals():
    frame = close_frame([10, 11, 12, 13, 12, 11, 10, 9])
    ema = EMACrossoverStrategy(fast_period=2, slow_period=4).generate_signals(frame)
    rsi = RSIMeanReversionStrategy(period=3).generate_signals(frame)
    assert set(ema["signal"].unique()).issubset({-1, 0, 1})
    assert set(rsi["signal"].unique()).issubset({-1, 0, 1})


def test_portfolio_buy_and_sell_updates_cash_and_quantity():
    portfolio = Portfolio(initial_capital=1000)
    portfolio.execute_trade(
        Trade("AAA", OrderSide.BUY, 2, 100, pd.Timestamp("2024-01-01"))
    )
    assert portfolio.cash == 800
    assert portfolio.positions["AAA"].quantity == 2
    portfolio.execute_trade(
        Trade("AAA", OrderSide.SELL, 1, 120, pd.Timestamp("2024-01-02"))
    )
    assert portfolio.cash == 920
    assert portfolio.positions["AAA"].quantity == 1


def test_backtest_is_chronological_and_does_not_trade_outside_range():
    frame = close_frame([10, 10, 10, 10])
    strategy = EMACrossoverStrategy(fast_period=2, slow_period=3)
    result = BacktestEngine(initial_capital=1000, quantity=1).run(
        {"AAA": frame},
        strategy,
        start_date="2024-01-02",
        end_date="2024-01-03",
    )
    assert result.equity_curve.index.min() == pd.Timestamp("2024-01-02")
    assert result.equity_curve.index.max() == pd.Timestamp("2024-01-03")
    assert all(trade.timestamp <= pd.Timestamp("2024-01-03") for trade in result.trades)
