# algo_engine

Algorithmic trading research and backtesting platform (NSE). Start with `CLAUDE.md` and `docs/`.

All commands run from the project root. Every `python -m algo_engine...` command needs `PYTHONPATH=src` (or `export PYTHONPATH=src` once per shell).

## 1. Setup
```
/Library/Frameworks/Python.framework/Versions/3.12/bin/python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

Credentials go in an untracked `.env` file or in environment variables. Never commit them.

| Variable | Used for | Notes |
|---|---|---|
| `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` | source market-data DB (read-only) | required for `data.load`, `data.coverage` and the integration tests |
| `ALGO_PGHOST`, `ALGO_PGPORT`, `ALGO_PGDATABASE`, `ALGO_PGUSER`, `ALGO_PGPASSWORD`, `ALGO_SCHEMA` | local store (schema `algo`) | defaults: unix socket `/tmp`, database `postgres`, OS user, schema `algo` |

`data.load` refuses to run if the local store points at the source database.

## 2. Run the tests
```
.venv/bin/pytest tests/unit
.venv/bin/pytest tests/integration        # live, read-only; needs the PG* variables
```

## 3. Load the data into the local store
```
export PYTHONPATH=src
.venv/bin/python -m algo_engine.data.load --migrate-only        # first time only: create schema and tables
.venv/bin/python -m algo_engine.data.load --start 2018-01-01    # load Nifty 50 bars, adjust, store dividends
.venv/bin/python -m algo_engine.data.load --dividends-only      # optional: rebuild only the dividends table
.venv/bin/python -m algo_engine.data.coverage                   # optional: per-year coverage check (read-only)
```
`data.load` reads the source DB and replaces the stored bars of the loaded symbols. Options: `--end YYYY-MM-DD`, `--fin-codes 1 2 3` (explicit symbols instead of the Nifty 50 members). `data.coverage` accepts `--start`, `--end`, `--csv out.csv`.

Re-run `data.load` whenever the source tables `stock_prices_five_years` or `corporate_actions` change.

## 4. Run a backtest, score it, build the report
```
.venv/bin/python -m algo_engine.engine.run --config configs/ema_crossover_nifty50_index.yaml --out runs
.venv/bin/python -m algo_engine.analytics.run --run-dir runs/<run_id> --report
open runs/<run_id>/report.html
```
The first command prints `run_id=<16 characters>` and writes `runs/<run_id>/` (config, events, equity, trades, manifest). Use that id in the second command, which adds `metrics.json`, `benchmark.csv` and `report.html`.

`<run_id>` is the backtest id. It is not the `run_id` printed by `data.load`, which counts data loads.

Options for `analytics.run`: `--rf 0.065` (annual risk-free rate), `--no-benchmark`, `--benchmark <fin_code>`, `--bootstrap 2000`. Rebuild a report from a run directory: `.venv/bin/python -m algo_engine.reports.build --run-dir runs/<run_id>`.

Strategy, symbols, dates, capital, costs and slippage are set in the YAML file (see `configs/` and the docstring of `src/algo_engine/engine/config.py`).

## 5. Parameter experiments (grid, train/test split, walk-forward)
```
# Grid search on the whole window (labelled in-sample)
.venv/bin/python -m algo_engine.research.run --config configs/ema_crossover_nifty50_index.yaml --grid configs/grid_ema.yaml

# Select on the data before 2022-01-01, evaluate on 2022-01-01 onward
.venv/bin/python -m algo_engine.research.run --config configs/ema_crossover_nifty50_index.yaml --grid configs/grid_ema.yaml --test-start 2022-01-01

# Rolling walk-forward: 36 months train, 12 months test
.venv/bin/python -m algo_engine.research.run --config configs/ema_crossover_nifty50_index.yaml --grid configs/grid_ema.yaml --walk-forward 36 12
```
Options: `--objective sharpe|cagr|total_return|calmar`, `--rf 0.065`, `--mc 2000` (Monte Carlo resamples), `--out runs/experiments`. Results go to `runs/experiments/<experiment_id>/`: `summary.json`, `registry.csv` (every trial), and SVG heatmaps. The grid file maps config paths to candidate values (see `configs/grid_ema.yaml`).

## Typical order
1. `data.load --start 2018-01-01` (after any change to the source tables)
2. `engine.run --config <yaml>`
3. `analytics.run --run-dir runs/<run_id> --report`
4. `research.run --config <yaml> --grid <yaml> --test-start <date>` before trusting any parameter choice
