# Decision Log (ADR)

Append-only. Never edit or delete an accepted decision. To change one, add a new ADR and mark the old one `Superseded by ADR-XXXX`.

Statuses: **Accepted** (user approved), **Proposed** (recommended, awaiting user approval), **Superseded**.

Template:

```
## ADR-NNNN: Title
- Date:
- Status:
- Decision:
- Context:
- Alternatives considered:
- Reason for selection:
- Trade-offs:
```

---

## ADR-0001: Custom event-driven backtesting engine
- Date: 2026-11-20
- Status: Accepted (stated by user in project brief)
- Decision: Build a custom event-driven engine that processes bars chronologically and passes events through strategy, order, risk, execution, and portfolio components.
- Context: The project aims to eventually share the strategy/order/portfolio code with paper trading and live trading. The user wants control over fill realism and look-ahead prevention.
- Alternatives considered: (a) Vectorized pandas backtests (fast, but fill realism, position state and path-dependent rules are awkward, and live parity is poor); (b) existing frameworks such as backtrader, vectorbt, Zipline-reloaded, bt (mature, but backtrader is poorly maintained, and none match our NSE cost model and live-parity goals without adaptation).
- Reason for selection: Same code path for backtest/paper/live; explicit control of timing and costs; the research goal values understanding every step.
- Trade-offs: More code to write and test; slower than vectorized for large parameter sweeps. Mitigation: vectorized indicator precomputation (look-ahead-safe) inside the event loop, and a vectorized fast path later only for screening, validated against the engine.

## ADR-0002: Repository layout and Python version
- Date: 2026-11-20
- Status: Accepted by user 2026-11-20 (dependency file amended by ADR-0009)
- Decision: `src/` layout with package `algo_engine`; `tests/` split into `unit/` and `integration/`; `configs/` for YAML run configs; `docs/`. Python 3.12 in a project venv, dependencies in `pyproject.toml`.
- Context: Repository is empty (README only). PyCharm SDK is Python 3.12, system `python3` is 3.10 and has no pandas.
- Alternatives considered: flat layout; requirements.txt only; Python 3.13 (installed via Homebrew).
- Reason for selection: `src/` layout prevents accidental imports of uninstalled code and is standard for packaged projects; 3.12 matches the IDE configuration and is well supported by pandas/NumPy.
- Trade-offs: Slightly more setup. Decision is cheap to reverse before code exists.

## ADR-0003: Database access via SQLAlchemy Core + psycopg, read-only
- Date: 2026-11-20
- Status: Accepted by user 2026-11-20
- Decision: A `MarketDataProvider` interface with a PostgreSQL implementation using SQLAlchemy Core (text/parameterized SQL) and psycopg 3, returning pandas DataFrames. The connection uses a read-only role/transaction and settings from environment variables (`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, or a `DATABASE_URL`). Source is the remote `univest` database (see ADR-0008). Never hardcode credentials.
- Context: No existing application code or connection config exists in the repo. The local server uses OS-user auth over a unix socket. The source tables have no keys or indexes.
- Alternatives considered: raw psycopg only (fewer dependencies, but weaker pooling/URL handling); full ORM (unneeded for read-only analytical queries); pandas `read_sql` with a raw DBAPI connection (deprecated warnings).
- Reason for selection: Parameterized queries, URL-based config and portability with minimal abstraction.
- Trade-offs: One extra dependency (SQLAlchemy). The abstraction (`MarketDataProvider`) allows swapping to Parquet or a broker feed without engine changes.

## ADR-0004: Prices are normalized into a clean, validated bar store; raw table stays untouched
- Date: 2026-11-20
- Status: Proposed
- Decision: Never query `stock_prices_five_years` directly from the engine. A data layer reads it, derives `trading_date` (IST date from `updated_time` ms), dedupes on `(fin_code, trading_date)`, validates OHLC, and emits a canonical bar DataFrame. Results may be cached as Parquet (or a new, separately named table, via a documented migration) keyed by a data fingerprint.
- Context (verified): the table has no PK/unique/index; `updated_time` is epoch ms with era-dependent time-of-day; 66 exact duplicate `(fin_code, updated_time)` pairs and further same-date duplicates; 2 rows with zero O/H/L; `created_at` mixes seconds and ms; locally it holds only 3 index series, not Nifty 50 equities.
- Alternatives considered: fix the source table in place (violates the no-schema-change rule, and risks other apps using it); query raw data on each run (non-reproducible, slow without indexes).
- Reason for selection: Safe, reproducible, and keeps source data intact.
- Trade-offs: Another storage layer to maintain. Duplicate resolution rule (keep latest `created_at`) must be documented and logged.
- Open dependency: Nifty 50 equity history must be located/loaded. See PROJECT_PLAN Known Issues K1.

## ADR-0005: Price basis (raw vs adjusted) must be established before any equity backtest
- Date: 2026-11-20
- Status: Proposed (decision pending user input)
- Decision: The engine will carry an explicit `price_basis` property (`raw` or `adjusted`) on its data. Backtests on equities must not run on unadjusted data across splits/bonuses. Preferred approach: obtain split/bonus-adjusted OHLC (and dividend-adjusted for signals if desired) and store the adjustment factors; fill/valuation conventions follow the Financial Simulation Rules in CLAUDE.md.
- Context: No adjustment-factor column exists, `corporate_actions` is empty locally, and the equity price data is not present, so it cannot be verified whether equity prices are adjusted.
- Alternatives considered: use raw prices and apply corporate actions in the portfolio (most faithful for live parity, but needs reliable action data); use vendor-adjusted prices (simplest, but fills at adjusted prices differ from actual traded prices, so costs, tick rounding and lot checks are slightly wrong); detect splits heuristically from price gaps (fallback only, never primary).
- Reason: Needs data answers first. Recommendation for the MVP: adjusted prices for both signal and fills, flagged as an approximation in reports.
- Trade-offs: See alternatives. Pending user answer to question Q2.

## ADR-0006: Execution model defaults for daily bars
- Date: 2026-11-20
- Status: Proposed
- Decision: Signals on bar `t` close → market orders fill at bar `t+1` open ± slippage. Stop/limit semantics and same-bar stop+target ambiguity follow CLAUDE.md §6.3 (pessimistic).
- Context: Daily OHLC does not reveal the intraday path; same-bar close execution would use information the strategy could not act on.
- Alternatives considered: fill at signal-bar close (unrealistic; look-ahead-adjacent); fill at next-bar VWAP/close (needs data we lack or lags unrealistically); randomized intrabar path (non-deterministic by default).
- Reason for selection: Simple, defensible, deterministic.
- Trade-offs: Open-price fills ignore opening-auction impact; slippage parameter accounts for it. Configurable alternatives can be added through an `ExecutionModel` interface.

## ADR-0007: Plan-level choices for the MVP scope
- Date: 2026-11-20
- Status: Proposed
- Decision: MVP = milestones 1–8 plus a minimal static report (part of 9), single timeframe (daily), multiple symbols supported by data model but with one symbol run first, market and (simple) stop orders only, EMA crossover as the pipeline-validation strategy. Streamlit, optimization, paper trading and Kite are post-MVP.
- Context: User wants a simple strategy to validate the pipeline and not to over-engineer.
- Alternatives considered: including the dashboard in MVP; implementing several strategies at once.
- Reason: Smallest vertical slice that proves correctness.
- Trade-offs: Visualization arrives later; early results are read from files/notebook output.

## ADR-0008: Remote `univest` database is the source of historical data (read-only)
- Date: 2026-11-20
- Status: Accepted (user directed use of the remote DB)
- Decision: Historical bars and `company_master` are read from the remote PostgreSQL `univest` DB. Access is read-only (`default_transaction_read_only=on`, `statement_timeout`), always filtered by `fin_code`, with NSE rows only. Credentials are supplied via environment variables; none are stored in the repo or docs.
- Context (verified): Local DB has only 3 index series; remote has equities from 2018 to 2026-09-21 for the 7 large caps checked. Table is ~21M rows with no PK/unique constraint; indexes exist on `fin_code` only (plus price columns).
- Alternatives considered: local DB (lacks equities); full copy of the remote table (unnecessary size); direct engine reads from remote on each run (slow, non-reproducible, load on a production-style DB).
- Reason for selection: It is where the data is, and per-`fin_code` queries are fast with the existing index. Normalized per-symbol bars are cached locally (ADR-0004) so runs are reproducible and do not hit the remote DB.
- Trade-offs: Network dependency and exposure of a shared DB. Mitigation: read-only role, cached snapshots with a data fingerprint, rotate the pasted password (PROJECT_PLAN K14).

## ADR-0009: Scope, universe, adjustments, and dependency file (user answers)
- Date: 2026-11-20
- Status: Accepted (user answers)
- Decision: (1) Corporate adjustments are derived from the `corporate_actions` table, which the user states is reliable (supersedes the open question in ADR-0005; the raw-vs-adjusted fill convention still needs to be fixed in M3, default per ADR-0005). (2) The Nifty 50 universe is read from `index_companies_mapping` (row `fin_code=100256265`, comma-separated member fin_codes), as a current snapshot. (3) Product scope is delivery (CNC), long-only, cash account. (4) Normalized bars are stored in a new table, never in the source tables (confirms ADR-0004; location is open item O2). (5) Dependencies are tracked in `requirements.txt` (pinned), kept in sync with code; this amends ADR-0002, which proposed `pyproject.toml`. Python 3.12 venv and `src/` layout stay.
- Context: User answered Q1-Q8 on 2026-11-20. Verified: the Nifty 50 row has 50 members, all with NSE price rows, though HDFCBANK NSE history begins only 2025-06-18 and 12 other members start after 2018.
- Alternatives considered: pyproject.toml with extras (more standard for packaging, but the user asked for requirements.txt); point-in-time constituents (not available).
- Reason for selection: Follows the user's direction and uses existing data.
- Trade-offs: Current-snapshot membership keeps survivorship bias, so reports must carry that label. requirements.txt does not separate runtime and dev tools unless we add `requirements-dev.txt`. Long-only/delivery excludes shorting and intraday strategies (e.g. pairs trading needs a later ADR).
so 
## ADR-0010: Event-reconciled price adjustment and explicit data-gap handling
- Date: 2026-11-20
- Status: Accepted (user: "let's do this"); implemented in `data/adjust.py` with two refinements: jumps spanning up to 5 missing days can match an event, and a gap in the search window is never read as "already adjusted". Added `find_unexplained_jumps` because `corporate_actions` misses recent events (K20)
- Decision: Build the adjusted close series from raw NSE bars plus deduplicated split/bonus events from `corporate_actions` (kinds via `corpact_id`: 76/68 bonus, 90/91 split; rights 69/78 deferred). For each event, compare the price ratio across the ex-date with the expected factor. If the ratio is near the factor, the vendor has not adjusted it, so apply it. If the ratio is near 1, treat it as already adjusted and do not apply it. If neither fits (e.g. wrong ex-date, missing bars), flag the event as ambiguous in the quality report and exclude the symbol-period from backtests until resolved. Cash dividends are kept as events and credited to cash in the portfolio on the ex-date (not folded into prices).
- Context (verified, PROJECT_PLAN K15-K18): event data is duplicated across ids; the vendor adjusted some past events and not others; some ex_dates are wrong; NSE bars have missing stretches.
- Alternatives considered: (a) apply all corporate actions to raw prices (double-adjusts events already adjusted); (b) trust the stored series as-is (Reliance, Wipro, Shriram, Dr Reddy would show false crashes of 50-80%, producing false signals and stops); (c) obtain a clean adjusted source elsewhere (best quality, depends on data availability).
- Reason for selection: Works with the data we have and fails loudly on ambiguity, consistent with the rule never to silently use invalid market data.
- Trade-offs: More logic and a tolerance parameter for matching factors; some symbol-periods will be excluded; dividends are not in the price series so benchmark and strategy returns compare on a price basis plus explicit dividend cash.

## ADR-0011: Hybrid event handling, YAML run configs
- Date: 2026-11-23
- Status: Accepted (user: "let's move with hybrid, add yaml too")
- Decision: The engine loop is explicit and owns the order of phases (ARCHITECTURE section 4). Components exchange immutable events through an `EventSink` interface. In a backtest the sink is an in-memory `EventLog` that stamps a sequence number and the run writes it to `events.jsonl`. There is no publish/subscribe bus. Run configs are frozen dataclasses (`RunConfig`) loaded from YAML (PyYAML, `yaml.safe_load`) or a dict, validated at load, and stored as a canonical JSON snapshot. The run id is a hash of the snapshot plus the data fingerprint.
- Context: The key rule is that an order created at `t` fills no earlier than `t+1`. A bus makes that depend on subscriber order and re-entrant publishing. The loop enforces it and raises `ExecutionError` if a component breaks it.
- Alternatives considered: synchronous in-process bus with fixed handler order (about 60 extra lines plus ordering tests, no current consumer); async bus (not needed for daily bars).
- Reason for selection: Keeps ordering visible and testable while leaving a seam: paper/live runners can pass a sink that forwards events, with no change to strategy, order, risk or portfolio code.
- Trade-offs: A bus is not available for free later. Adding one means a new sink plus moving handler registration, and the loop would still own the phase order. New dependency: PyYAML 6.0.4 (YAML is the requested config format).
- Known limit: the look-ahead guard compares truncated and full runs, so it catches strategies that read engine-supplied data beyond the view. A strategy that reads a file or global independent of the engine input is not detectable this way.
