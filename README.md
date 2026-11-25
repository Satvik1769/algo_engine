# algo_engine

Algorithmic trading research and backtesting platform (NSE). Start with `CLAUDE.md` and `docs/`.

## Setup
```
/Library/Frameworks/Python.framework/Versions/3.12/bin/python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest tests/unit
```
Live (read-only) integration tests: export `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, then `.venv/bin/pytest tests/integration`. Never commit credentials.

## Load the bars table (local Postgres)
```
PYTHONPATH=src .venv/bin/python -m algo_engine.data.load --start 2018-01-01
```
Reads the source DB (read-only, `PG*` vars) and writes schema `algo` in the local DB (`ALGO_PG*` vars, default unix socket `/tmp`). Re-running replaces the stored bars of the loaded symbols.
`--dividends-only` rebuilds just the dividends table from the stored bars. Run `--migrate-only` once on an existing store to add the dividends table.

## Run a backtest, score it, build the report
```
PYTHONPATH=src .venv/bin/python -m algo_engine.engine.run --config configs/ema_crossover_nifty50_index.yaml --out runs
PYTHONPATH=src .venv/bin/python -m algo_engine.analytics.run --run-dir runs/<run_id> --report
```
The first command writes `runs/<run_id>/` (config, events, equity, trades, manifest). The second adds `metrics.json`, `benchmark.csv` and `report.html` (open it in a browser). Options: `--rf 0.065` (annual risk-free rate), `--no-benchmark`. `python -m algo_engine.reports.build --run-dir runs/<run_id>` rebuilds the report from the files in the run directory.
