# Project Plan

Last updated: 2026-11-20 (documentation setup session).
**Current phase: Milestone 1 (repository and data analysis) is complete; data source located (remote `univest` DB). Waiting for user approval and answers to open questions before starting Milestone 2.**

Update this file after every task: status table, checkboxes, Known Issues, Next Recommended Tasks.

## 1. Milestone Status

| # | Milestone | Priority | MVP | Status |
|---|---|---|---|---|
| 1 | Repository and data infrastructure analysis | P0 | Yes | **Done** (docs only) |
| 2 | Historical data access layer | P0 | Yes | Not started |
| 3 | Data validation and normalization | P0 | Yes | Not started |
| 4 | Core event-driven engine | P0 | Yes | Not started |
| 5 | Strategy interface and indicator engine | P0 | Yes | Not started |
| 6 | Order management and execution simulation | P0 | Yes | Not started |
| 7 | Portfolio management | P0 | Yes | Not started |
| 8 | Performance analytics | P0 | Yes | Not started |
| 9 | Reports and dashboard | P1 | Minimal static report only | Not started |
| 10 | Optimization and robustness testing | P1 | No | Not started |
| 11 | Paper trading | P2 | No | Not started |
| 12 | Zerodha Kite integration | P2 | No | Not started |

Suggested order: build 4–8 as one thin vertical slice early (a dummy always-in-market strategy on index data, which exists locally) so the pipeline is proven before data for equities arrives. Order: 1 → 2 → 3 → (5, 6, 7 interfaces) → 4 → 8 → 9. Milestone 4 needs minimal versions of 5–7; develop them together in small steps, with tests, and mark each milestone Done only when its own acceptance criteria are met.

**MVP definition:** Milestones 1–8 + a minimal static report (from 9), daily bars, long-only, cash account, market (and simple stop) orders, EMA crossover strategy, cost+slippage model, metrics and benchmark comparison, look-ahead and reconciliation tests.

## 2. Technical Decisions (summary; full ADRs in DECISIONS.md)

| ADR | Decision | Status |
|---|---|---|
| 0001 | Custom event-driven engine | Accepted |
| 0002 | `src/` layout, Python 3.12 venv, pyproject | Proposed |
| 0003 | SQLAlchemy Core + psycopg, read-only | Proposed |
| 0004 | Normalized bar layer; source tables untouched | Proposed |
| 0005 | Price basis (raw/adjusted) | Proposed, needs user input |
| 0006 | Next-open fills, pessimistic stop/target ordering | Proposed |
| 0007 | MVP scope | Proposed |

## 3. Known Issues (verified 2026-11-20 against local DB `postgres`)

- **K1 (resolved 2026-11-20, scope still to verify):** The local DB only has 3 index series, but the remote `univest` DB (read-only; credentials via env, never in repo) has equity history. Seven large caps checked (RELIANCE 100325, TCS 132540, INFY 100209, HDFCBANK 100180, ICICIBANK 132174, SBIN 100112, ITC 100875): 2018-01-01 → 2026-09-21. Still unverified: the full Nifty 50 list, per-symbol NSE coverage (HDFCBANK NSE starts 2025-06-18 only), and survivorship.
- **K11:** Prices are **unadjusted** (RELIANCE ~₹2,611 in Jan 2024 vs ~₹1,240 in 2026 after the bonus). Corporate actions exist remotely (93,938 rows) but their quality is unverified. Adjustment is required before backtesting (ADR-0005).
- **K12:** Remote table mixes `stock_type` NSE and BSE; BSE data is noisier. Use NSE only unless NSE is missing. Duplicate rows per date are common (RELIANCE NSE: 1,914 rows for 1,852 dates).
- **K13:** Remote table is ~21M rows; unfiltered aggregates exceed a 60 s timeout. Always filter by `fin_code`; cache normalized bars locally.
- **K14 (security):** A production-style DB password was pasted in chat. It is not stored in the repo. Recommend rotating it, creating a dedicated read-only role for this project, and restricting the host/IP.
- **K2:** The source table has no PK/unique constraint/index; duplicates exist: 66 `(fin_code, updated_time)` pairs (identical OHLC) and additional same-IST-date rows with different timestamps (Nifty 50: 2,567 rows vs 2,502 distinct dates).
- **K3:** No Nifty 50 TRI series available (`indices` empty). Nifty 50 price index exists as fin_code 100256265. Need a TRI source (NSE/niftyindices CSV) or must use price index with an explicit label.
- **K4:** Zero-valued O/H/L in 2 rows (2025-01-06 IST, Nifty 50 and Midcap 150). 265 null `open_price` rows. Index volume is 0/NULL.
- **K5:** `updated_time` time-of-day inconsistent across eras (00:00, 05:30, 16:00, 16:05 IST…); `created_at` mixed units. Always derive dates in `Asia/Kolkata`.
- **K6:** Gaps >4 calendar days appear in Nifty 50 (3), Midcap 150 (3, max 20 days), Smallcap 250 (2). Not yet compared against an NSE calendar (no valid calendar locally; `holiday_list` has 15 rows, all 2022-01-26). Missing days vs real holidays are undetermined.
- **K7:** Corporate actions: `corporate_actions` table exists but is empty locally. Adjustment status of equity prices unknown.
- **K8:** No survivorship-safe Nifty 50 membership history. Current constituents only [ASSUMPTION: list must be provided or fetched].
- **K9:** No `.gitignore`, no dependency file, no venv. `.idea/` is untracked state in the repo directory. The only commit is "Initial commit".
- **K10:** `company_master` is large (939,667 rows, mostly derivatives); equity filter is `segment='E' AND instrument_type IS NULL AND NOT deleted` (21,758 rows). Filtering must use explicit `fin_code` lists for the Nifty 50.

## 4. Open Questions (need user answers; see end of chat summary)

- Q1 (answered: remote `univest` DB). Follow-up: may we create a read-only role, and may we copy normalized bars to local Parquet/Postgres?
- Q2 (partly answered: prices are unadjusted). Follow-up: is `corporate_actions` reliable enough to compute adjustment factors, or do we get adjusted data from elsewhere?
- Q3: Source and acceptance for the Nifty 50 constituent list and the Nifty 50 TRI series?
- Q4: Cost model scope: delivery (CNC) only, or intraday (MIS) too? Initial capital (e.g. ₹10 lakh)?
- Q5: Long-only for the MVP, or allow short selling?
- Q6: Approve Python 3.12 venv + `src/` layout + SQLAlchemy/psycopg (ADR-0002/0003)?
- Q7: Is it acceptable to create a new schema/table or Parquet cache for normalized bars (ADR-0004)?
- Q8: Should the git repo get a `.gitignore` (venv, .env, .idea, data, caches)?

## 5. Milestone Details

### Milestone 1: Repository and data infrastructure analysis — DONE
- Objective: Establish verified facts about repo and data. Scope: read-only inspection, docs. Components: none.
- Tasks: [x] repo inventory [x] DB discovery [x] schemas [x] fin_code relationship [x] column availability [x] completeness/duplicates/gaps [x] write docs.
- Deliverables: CLAUDE.md, ARCHITECTURE.md, PROJECT_PLAN.md, DECISIONS.md.
- Acceptance: facts labeled verified; no code or schema changed. ✔
- Outcome: Findings in Known Issues K1–K10.

### Milestone 2: Historical data access layer
- Objective: Provide a read-only, parameterized, configurable way to fetch raw and instrument metadata for given fin_codes/dates.
- Scope: Postgres provider, config/env handling, instrument lookup by symbol/fin_code. No cleaning logic (that is M3).
- Components: `data/provider.py` (interface), `data/postgres_provider.py`, `config/`.
- Tasks: [ ] project scaffold (pyproject, venv, pytest, ruff) [ ] `.gitignore` [ ] DB settings via env [ ] `get_instruments(symbols)` [ ] `get_raw_bars(fin_codes, start, end)` [ ] read-only connection [ ] test DB fixtures.
- Dependencies: M1; Q1, Q6.
- Deliverables: provider class + tests + README setup notes.
- Acceptance: returns raw rows for the Nifty 50 index and sample equities; no writes possible; no credentials in code; parameterized SQL only.
- Tests: unit (SQL building/param binding, config), integration against a temporary schema/fixture, error on unknown fin_code, empty range, DB down.
- Risks/edge cases: no indexes → slow scans (add a documented migration index only with approval); `numeric` → float conversion; timezone; unknown symbol; multiple fin_codes per symbol.

### Milestone 3: Data validation and normalization
- Objective: Turn raw rows into trustworthy canonical bars with a quality report.
- Scope: IST date derivation, dedupe, OHLC sanity (H≥max(O,C), L≤min(O,C), >0), NaN handling, gap detection against a trading calendar, volume handling, price-basis flag, fingerprint.
- Components: `data/validation.py`, `data/normalize.py`, `data/calendar.py`.
- Tasks: [ ] date derivation [ ] dedupe rule (latest `created_at`; log) [ ] OHLC validators [ ] quarantine/raise policy [ ] gap report [ ] NSE trading calendar source [ ] data fingerprint [ ] optional Parquet cache.
- Dependencies: M2; Q2, Q3.
- Deliverables: `normalize_bars()` + `DataQualityReport`.
- Acceptance: the 2 zero-OHLC rows and 66 duplicate pairs are detected and reported; output unique on `(fin_code, trading_date)`; deterministic fingerprint; no silent drops.
- Tests: duplicates with differing values, zero/NaN/negative prices, unsorted input, ms vs s timestamps, DST-free IST conversion, holiday vs missing day.
- Risks: duplicate resolution wrongly choosing stale data; unadjusted splits (see ADR-0005); calendar source.

### Milestone 4: Core event-driven engine
- Objective: Chronological loop wiring components with the event lifecycle in ARCHITECTURE.md §4.
- Scope: single timeframe (daily), 1..N symbols, synchronous, deterministic, event log, run directory output.
- Components: `engine/`, `events.py`, `config/RunConfig`.
- Tasks: [ ] event dataclasses [ ] loop & ordering rules [ ] HistoryView (truncated) [ ] run config + snapshot [ ] event log [ ] seed handling.
- Dependencies: M3 output schema; interfaces from M5–M7 (stubs first).
- Acceptance: runs a no-op strategy over index data; identical output on rerun; no event at `t` references `t+1`.
- Tests: look-ahead truncation test, determinism, ordering (fill for orders created at `t` occurs ≥ `t+1`), multi-symbol calendar alignment.
- Risks: calendar misalignment across symbols; symbols with late listing/suspension; ordering ambiguity.

### Milestone 5: Strategy interface and indicator engine
- Objective: Plug-in strategies and causal indicators.
- Scope: `Strategy` ABC, `Signal`, param validation, strategy registry, indicators EMA, SMA, RSI, ATR, Bollinger, rolling returns (add as needed per strategy).
- Tasks: [ ] interface [ ] registry/config loading [ ] indicators with tests vs hand values [ ] causality tests [ ] EMA crossover strategy.
- Dependencies: M4 (parallel). Deliverables: EMA crossover running end to end.
- Acceptance: new strategy added with no engine edits; indicators match reference computations; warm-up handled explicitly (no signals before enough history).
- Tests: indicator unit tests, warm-up, causality, param validation, signal on exact crossover bar.
- Risks: pandas `ewm` seeding conventions (`adjust`); NaN warm-up; accidental `shift(-1)`.

### Milestone 6: Order management and execution simulation
- Objective: Orders with state machine; realistic fills with cost and slippage.
- Scope: MARKET orders, simple STOP; DAY validity; cost model; slippage; risk checks (cash, max position %, max open positions, lot/tick rounding).
- Tasks: [ ] Order/State machine [ ] signal→order sizing policy [ ] risk manager [ ] execution model [ ] cost model (rates in config) [ ] slippage model.
- Dependencies: M4, M5, K-data on tick/lot. Acceptance: signal at `t` never fills before `t+1`; costs reconcile; rejected orders logged with reason.
- Tests: gap-through stop, stop & target same bar, insufficient cash, zero quantity, cost breakdown against hand calc, order state transitions.
- Risks: wrong STT/charge rates; limit-price semantics; unfillable orders at limit-up/down (circuit) not modeled.

### Milestone 7: Portfolio management
- Objective: Correct accounting.
- Tasks: [ ] cash/positions [ ] avg-cost P&L [ ] mark-to-market [ ] equity curve [ ] trade log (round trips) [ ] corporate action hooks (stub).
- Acceptance: invariants and reconciliation hold on randomized fill sequences.
- Tests: property tests, partial close, re-entry, fees, dividends/splits (when implemented).
- Risks: rounding drift; partial-position cost basis; costs counted twice.

### Milestone 8: Performance analytics
- Objective: Metrics in ARCHITECTURE.md §8, benchmark comparison.
- Tasks: [ ] metrics module [ ] benchmark loader (Nifty 50 price index now; TRI when available) [ ] trade stats [ ] bootstrap CIs.
- Dependencies: M7, K3. Acceptance: metrics match hand-computed fixtures; edge cases defined.
- Tests: flat equity, single trade, no losing trades (profit factor), zero-volatility Sharpe, short history, all-negative returns.
- Risks: annualization conventions; rf rate choice; benchmark date alignment.

### Milestone 9: Reports and dashboard
- Tasks: [ ] static HTML/markdown report with equity, drawdown, trades, metrics, config, data fingerprint and caveats [ ] Plotly charts [ ] Streamlit app (post-MVP).
- Acceptance: report reproducible from run directory; caveats (survivorship, in-sample, costs) are always printed.

### Milestone 10: Optimization and robustness
- Tasks: [ ] parameter grid runner with run registry [ ] walk-forward and train/test split [ ] parameter-sensitivity heatmaps [ ] Monte Carlo/bootstrapped trade shuffles [ ] multiple-testing correction (e.g. deflated Sharpe) [ ] cost/slippage stress tests [ ] remaining roadmap strategies.
- Acceptance: results show out-of-sample vs in-sample and number of trials.

### Milestone 11: Paper trading
- Tasks: [ ] live/near-live data provider [ ] `PaperBroker` [ ] scheduler/runner [ ] state persistence [ ] reconciliation vs backtest. Dependencies: M4–M8, Kite market data decisions.

### Milestone 12: Zerodha Kite integration
- Tasks: [ ] auth flow (daily token) [ ] instrument mapping via `kite_trading_symbol` [ ] `KiteBroker` [ ] order safeguards (kill switch, max order value, dry-run default) [ ] audit log. Dependencies: M11, Kite developer subscription. Risks: real money; secrets; rate limits; static IP requirements [ASSUMPTION, verify current Kite rules].

## 6. Completed Tasks

- 2026-11-20: Repository and database inspection (read-only); four documentation files created. No application code or DB changes.

## 7. Next Recommended Tasks

1. User answers the remaining questions (Q2 follow-up on corporate actions, Q3–Q8).
2. Milestone 2, task 1 (proposed first task): scaffold the project (pyproject, venv on Python 3.12, `.gitignore`, pytest) and implement a read-only `PostgresMarketDataProvider` with `get_raw_bars` and `get_instruments`, tested against the remote DB read-only (Nifty 50 index plus the seven large caps). No engine work yet.
3. Then Milestone 3 normalization with a deterministic data-quality report.
