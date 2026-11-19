# Algo Engine

A small, event-driven Python backtesting engine for historical daily OHLCV data.

```python
import pandas as pd
from algo_engine import BacktestEngine, EMACrossoverStrategy

data = pd.read_csv("prices.csv", parse_dates=["date"]).set_index("date")
result = BacktestEngine(initial_capital=100_000).run(
    {"NIFTY": data},
    EMACrossoverStrategy(fast_period=20, slow_period=50),
)
print(result.metrics)
```

The package includes historical data cleaning, EMA crossover, RSI mean-reversion,
Bollinger-band strategies, order and portfolio models, and performance analytics.