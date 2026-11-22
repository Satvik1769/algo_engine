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
