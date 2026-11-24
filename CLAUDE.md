# CLAUDE.md — Algo Engine Project Rulebook

This is the primary context file. Read it first in every session, then `docs/PROJECT_PLAN.md` (status and next task), `docs/ARCHITECTURE.md`, and `docs/DECISIONS.md`.

Labels used throughout the docs: **[VERIFIED]** = observed in the repo/DB on the date given; **[ASSUMPTION]** = not yet confirmed; **[RECOMMENDATION]** = proposal pending/after approval.

## 1. Project Vision

An algorithmic-trading **research and backtesting platform** for the Indian market (NSE), initially Nifty 50 stocks on **daily candles**.

Goals: develop strategies, backtest them realistically, measure them with quantitative metrics, compare strategies and parameter sets, find statistically meaningful edge, and eventually add paper trading and Zerodha Kite Connect execution.

This is quantitative research first. We want to know why a strategy works, in which regimes, and whether the result is statistically meaningful. A tool that emits BUY/SELL signals without simulation and evaluation is explicitly out of scope.

## 2. Current State (keep this section short and current)

- Repository: code under `src/algo_engine/` (config, data, indicators, strategy, engine) with tests under `tests/`; run a backtest with `python -m algo_engine.engine.run --config configs/<file>.yaml` (PYTHONPATH=src); `requirements.txt`, `requirements-dev.txt`, `.gitignore`, `pytest.ini`, and a project `.venv` (Python 3.12). Run tests with `.venv/bin/pytest`; live tests need `PGHOST/PGDATABASE/PGUSER/PGPASSWORD` set. [VERIFIED 2026-11-20 repo state]
- Python 3.12 is the PyCharm SDK setting; `python3` on PATH is 3.10 with no pandas installed. [VERIFIED]
- **Authoritative data source: remote PostgreSQL 15.18, host `34.14.174.6:5432`, database `univest`** (the app's production-style DB; user `postgres`). Credentials come from environment variables only (`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`) and must never be written to the repo, docs, or logs. All access is **read-only** (`default_transaction_read_only=on`, set a `statement_timeout`). The table is ~21M rows (691,796 fin_codes); unfiltered aggregates time out, so always filter by `fin_code`. [VERIFIED]
- A local Homebrew PostgreSQL (`/tmp:5432`, db `postgres`) holds a partial copy with indices only (no equities). Do not use it for backtests. [VERIFIED]
- Equity price history exists in the remote DB for Nifty 50 names (checked 7 large caps: 2018-01-01 → 2026-09-21; see §3). Prices are **unadjusted**. See ADR-0005 and ADR-0008. [VERIFIED]
- **Decisions from the user (2026-11-20):** `corporate_actions` is reliable and is the adjustment source; Nifty 50 membership comes from `index_companies_mapping`; **delivery (CNC) only, long-only** for now; Python 3.12 venv + `src/` layout + SQLAlchemy/psycopg approved; normalized bars go into a **new table** (not the source tables); dependencies are tracked in **`requirements.txt`** and must be kept in sync (ADR-0009).
- Milestone status lives in `docs/PROJECT_PLAN.md`. Do not duplicate it here.

## 3. Data Facts You Must Not Forget (verified; re-verify if in doubt)

`stock_prices_five_years` columns: `fin_code bigint`, `stock_type varchar` ('NSE' or 'BSE'), `open_price`, `close_price`, `high_price`, `low_price`, `volume_traded` (all `numeric`), `updated_time bigint`, `created_at numeric`, `last_day_close_price numeric`, `oi numeric`. The app's JPA entity declares the logical key `(fin_code, updated_time, stock_type)`, but the DB has **no PK/unique constraint**. Remote indexes: btree on `fin_code`, `(fin_code, high_price)`, `high_price`, and an expression index on `to_timestamp(updated_time/1000)`.

- Full OHLCV exists for equities (volume real; 0/NULL for indices). **Filter `stock_type='NSE'`**: rows are stored for both NSE and BSE, and BSE data is much noisier (e.g. RELIANCE: 283 zero-open rows on BSE vs 64 on NSE).
- Trading date = IST date of `updated_time` (epoch **milliseconds**). Time-of-day varies by era (23:00, 16:00, 00:00, 16:05, 15:55 IST...), so convert to `Asia/Kolkata` and take the date. Never key on raw timestamps. `created_at` is a load time with mixed units.
- **Duplicates are real**: RELIANCE NSE has 1,914 rows for 1,852 trading dates (53 dates with 2 rows, 3 with 3, 1 with 4); the loader must dedupe deterministically and report it. Zero-open/zero-OHLC rows also exist (RELIANCE NSE: 64 zero opens); reject or quarantine, never silently use.
- **Prices are unadjusted**: RELIANCE closed ~₹2,611 on 2024-01-01 (pre-bonus) versus ~₹1,240 in 2026. Corporate actions are needed (`corporate_actions` has 93,938 rows remotely; schema in the ADR-0005 work). `last_day_close_price` is available per row (useful for cross-checking).
- **Coverage is uneven**: HDFCBANK (fin_code 100180) NSE rows start only 2025-06-18 (older history is under BSE); similar gaps are likely for other names (mergers, relistings). Verify per symbol before use.
- Latest data date seen: 2026-09-21 (data may lag the current date).
- `company_master.fin_code` is the primary key and the join key. Equity rows: `segment='E'`, `instrument_type IS NULL`, `deleted` false/NULL. Index rows: `segment='I'`. `isin` is populated for the large caps checked; `kite_trading_symbol` was NULL for them (do not rely on it for Kite mapping).
- `holiday_list` has 61 rows remotely (15 locally, all 2022-01-26): coverage unverified, do not trust as a trading calendar yet. `indices` has 216 rows remotely (content unverified). No Nifty 50 constituent table, no membership history, and no Nifty 50 TRI series found yet. Nifty 50 index (fin_code 100256265) exists remotely: 2,702 rows 2015-10-16 → 2026-09-21, with 0 volume.

## 4. Architecture Principles

Details in `docs/ARCHITECTURE.md`.

1. **Event-driven.** A custom engine processes market events chronologically. Per bar: load data, update indicators, call strategy, signals, orders, risk validation, execution simulation, portfolio update, valuation, event/metric recording.
2. **Strategy-agnostic.** Adding a strategy must not require changes to the engine, portfolio, or order management. Strategies depend only on the `Strategy` interface and read-only market/portfolio views.
3. **Separation of concerns.** Independently maintainable components: Data Provider, Data Validation/Normalization, Indicator Engine, Strategy Engine, Signal Generation, Order Management, Risk Management, Execution Simulator, Portfolio Manager, Performance Analytics, Reporting.
4. **Realistic by construction.** The framework should make look-ahead impossible, not just discouraged (see §6).
5. **Extensible, not over-engineered.** Design seams for multi-symbol, multi-timeframe, portfolio strategies, other order types, paper/live brokers. Implement only what the current milestone needs.
6. **Deterministic.** Same data + config + seed = identical results. Persist the config and data fingerprint with each run.

## 5. Technology Decisions

Accepted/proposed stack (see ADRs for status):

| Area | Choice | Notes |
|---|---|---|
| Language | Python 3.12 | Matches PyCharm SDK. Create a project venv; do not use system 3.10. |
| Data | pandas, NumPy | Vectorized indicators and analytics; the engine loop itself is event-based. |
| DB access | SQLAlchemy Core + psycopg (v3) read-only queries | [RECOMMENDATION] ADR-0003 |
| Storage | PostgreSQL (existing) | Read-only from the engine. Optional Parquet cache for speed. |
| Config | YAML/TOML + pydantic or dataclasses; secrets from env | [RECOMMENDATION] |
| Tests | pytest | |
| Dependencies | `requirements.txt` (pinned versions) | Update it in the same change that adds/removes/upgrades any package. Keep dev tools in `requirements-dev.txt` if split. |
| Viz | Plotly | |
| Dashboard | Streamlit | After MVP. |
| Broker | Zerodha Kite Connect | Last milestones only. |

Do not add a dependency without a recorded reason (ADR if non-trivial).

## 6. Financial Simulation Rules (non-negotiable)

1. **No look-ahead.** At bar `t` the strategy sees data through the close of `t` only. Indicators at `t` use data ≤ `t`. Data structures handed to strategies must be truncated views; no strategy may index forward.
2. **No same-bar execution at the signal price.** A signal generated on the close of bar `t` creates an order that is eligible to fill no earlier than bar `t+1`. Default fill model: next-bar **open** (market orders). Any alternative is configured and documented.
3. **Daily-OHLC ambiguity.** The intraday path inside a daily bar is unknown. Defaults:
   - Market order fills at next open ± slippage.
   - Limit orders fill only if the bar's range crosses the limit (a price merely touched does not guarantee fill; a configurable conservative mode requires trading through).
   - Stop orders trigger if the range crosses the stop; fill at the stop, or at the open if the bar gaps through it (the worse price for the trader).
   - If a stop and a target are both hit in the same bar, assume the **stop was hit first** (pessimistic) unless configured otherwise.
   - Never use a bar's high/low to decide an entry that the same bar's close signaled.
4. **Costs and slippage are mandatory.** Every fill pays configurable brokerage, STT, exchange transaction charges, SEBI fees, stamp duty, GST, and slippage. Rates are configuration with an effective-date, not constants buried in code. Zero-cost runs are allowed only as an explicitly labeled diagnostic.
5. **Cash and positions.** Cash cannot go negative unless a margin model is explicitly enabled. No fractional shares unless configured. Lot/quantity rounding and minimum tick rounding are applied. Orders that cannot be afforded are rejected by risk, and the rejection is logged.
6. **P&L.** Realized P&L is computed at close of (part of) a position using a documented cost-basis method (default: average cost). Unrealized P&L is marked at the bar close. Equity = cash + market value of positions. Costs are deducted at fill time and reconcile exactly: `final_equity = initial_cash + realized_pnl + unrealized_pnl − total_costs` (if costs are not already inside realized P&L; document which).
7. **Corporate actions and adjusted prices.** The engine must declare whether it runs on raw or adjusted prices. Signals may use adjusted prices; fills/valuation must be consistent with the share count (split/bonus changes quantity; dividends are cash). Unadjusted series with splits create false signals. Treat unverified adjustment as a blocking risk (ADR-0005).
8. **Survivorship bias.** A Nifty 50 universe taken from *today's* constituents is biased. Until point-in-time membership exists, label results "current-constituents universe, survivorship-biased" in every report.
9. **Suspended/missing bars.** A missing bar is not forward-filled silently. Missing data is surfaced; policy per symbol is explicit (skip, carry with flag, or fail).
10. **Benchmark.** Compare against Nifty 50 **TRI** (total return). If only the price index is available, state it in the report; do not label it TRI.
11. **In-sample results are not evidence.** Never claim a strategy is profitable from in-sample backtests. Use train/test splits, walk-forward, out-of-sample periods, parameter-sensitivity, and multiple-testing awareness. Reports must show the number of parameter combinations tried.
12. **No real orders in backtests.** Broker adapters are unreachable from backtest code paths.

13. **Scope for now:** delivery (CNC) product only, long-only, cash account. No shorting, margin, or intraday (MIS) logic until a new ADR changes this.

## 7. Performance Metrics

Required: total return, CAGR, Sharpe, Sortino, max drawdown, win rate, profit factor, average holding period, number of trades, exposure. Plus benchmark-relative metrics.

Each formula is documented in `docs/ARCHITECTURE.md` (Performance section) with conventions: daily returns from equity curve, 252 trading days/year, risk-free rate configurable (default documented), sample std (ddof=1), Sortino with downside deviation relative to the target, CAGR over calendar years `(end/start)^(365.25/days) − 1`, exposure = time-weighted fraction of equity invested. Metrics must be unit-tested against hand-computed examples.

## 8. Engineering Conventions

1. Understand the existing code before modifying it. Reuse before creating.
2. One responsibility per module. No god classes.
3. Python 3.12, type hints on public functions, `from __future__ import annotations` where useful, `mypy`/`ruff` configuration once code exists.
4. Meaningful names: `close_price`, `fin_code`, `trading_date`; no unexplained abbreviations or magic numbers.
5. Money/prices: use `float` for vectorized research math; quantities are `int`. Round only at defined boundaries (tick size, final reports). If exact reconciliation proves necessary, record an ADR before switching to `Decimal`.
6. Trading parameters (periods, thresholds, costs, capital, slippage, risk limits) live in configuration with defaults, never hardcoded in logic.
7. Never swallow errors or invalid market data. Raise typed exceptions or record explicit data-quality issues. No bare `except`, no `except: pass`.
8. Document non-obvious financial calculations in the docstring with the formula, and in ARCHITECTURE.md when it affects behavior.
9. DB schema changes require a documented reason and a migration script (`migrations/`), plus an ADR. The engine's DB role must be read-only on source tables. Do not alter `company_master` or `stock_prices_five_years` without explicit user approval.
10. Secrets (DB passwords, Kite API key/secret/access token) come from environment variables or an untracked `.env`. Never commit them, log them, or print them. `.env` must be in `.gitignore`.
11. Proposed layout (confirm in ADR-0002 before creating): `src/algo_engine/{data,indicators,strategies,engine,orders,risk,execution,portfolio,analytics,reporting,config}`, `tests/{unit,integration}`, `configs/`, `docs/`, `scripts/`.
12. Dependencies: every new import of a third-party package requires a `requirements.txt` update in the same change, with a pinned version and a reason in the PR/summary. Never leave it stale.
13. Commit messages explain why. Do not commit unless asked.

## 9. Testing Requirements

- Unit tests for all business-critical logic: fill model, cost model, portfolio accounting, indicator math, metrics, risk rules, data validators.
- Property/invariant tests: equity reconciliation, cash never negative, position quantity = sum of fills, no fill timestamp earlier than order creation + 1 bar.
- **Look-ahead test** (mandatory): a strategy truncated at bar `t` produces identical signals to one given the full dataset, for every `t`. Also a deliberately cheating strategy must be caught by the guard.
- Integration tests: provider → validation → engine → analytics on small deterministic fixtures. DB tests use a fixture/test schema, never mutate the real tables.
- Golden-file test: a known simple strategy on a tiny dataset with hand-computed trades and equity.
- Determinism test: two runs with the same config produce byte-identical trade logs.
- Run `pytest` before declaring any task done; report what ran and what failed. Do not claim tests passed without running them.

## 10. Working Agreement with the User

- Before a major feature: understand context and dependencies, then write an implementation plan and wait for approval.
- Break work into small, testable steps. Explain important architectural decisions. List edge cases before implementing.
- Do not assume unestablished requirements; ask when a gap affects architecture or correctness.
- No unrelated improvements. Do not implement the whole project in one pass; work milestone by milestone.
- At the end of every substantial task: summarize what changed, tests run, remaining work, and update docs.
- Clearly separate verified facts, assumptions, and recommendations.

## 11. Documentation Maintenance Rules (most important)

A fresh session must be able to resume from the docs alone. Therefore, in the same change as the code:

| Event | Update |
|---|---|
| Any milestone/task progress | `docs/PROJECT_PLAN.md` (status table, task checkboxes, "Current Phase", "Next Recommended Tasks", "Known Issues") |
| Component, interface, data flow, lifecycle or formula changes | `docs/ARCHITECTURE.md` (must match the code; remove stale text) |
| Any important decision, or reversal of one | New entry in `docs/DECISIONS.md`. Never edit or delete old ADRs. Mark the old one `Superseded by ADR-XXXX`. |
| New verified data/schema fact | §3 of this file |
| New convention or rule | this file |

Keep entries dated. When a doc and the code disagree, the code is the truth; fix the doc immediately and note it. Do not leave "TODO: document" behind.

## 12. Do-Not List

- Do not modify existing DB schemas/data or write to source tables.
- Do not implement beyond the approved milestone.
- Do not call any broker API from backtest code.
- Do not print or commit credentials.
- Do not report backtest results without costs, benchmark, and the survivorship/in-sample caveats.
