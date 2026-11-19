# algo_engine

Algorithmic trading research and backtesting platform (NSE). Start with `CLAUDE.md` and `docs/`.

## Setup
```
/Library/Frameworks/Python.framework/Versions/3.12/bin/python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest tests/unit
```
Live (read-only) integration tests: export `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, then `.venv/bin/pytest tests/integration`. Never commit credentials.
