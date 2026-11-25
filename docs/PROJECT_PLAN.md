# Project Plan

Last updated: 2026-11-23 (M9 minimal report built; MVP milestones 1-9 done except the M3 data cleanup).
**Current phase: MVP pipeline complete: data, engine, orders, portfolio with dividends, analytics and a static HTML report. Next: M3 data cleanup and a stock-basket run once the 2024+ data is loaded, then M10 (optimization and robustness). M3 cleanup (K18, K19, ambiguous events) is still open. Proposals O2-O7 in section 4 are in effect unless the user changes them.**

Update this file after every task: status table, checkboxes, Known Issues, Next Recommended Tasks.

## 1. Milestone Status

| # | Milestone | Priority | MVP | Status |
|---|---|---|---|---|
| 1 | Repository and data infrastructure analysis | P0 | Yes | **Done** (docs only) |
| 2 | Historical data access layer | P0 | Yes | **Done** (2026-11-20) |
| 3 | Data validation and normalization | P0 | Yes | In progress (3a, 3b, 3c done; Parquet cache and open data decisions remain) |
| 4 | Core event-driven engine | P0 | Yes | **Done** (2026-11-23; runs with stub orders/execution/portfolio until M6/M7) |
| 5 | Strategy interface and indicator engine | P0 | Yes | **Done** (2026-11-23; EMA crossover runs end to end on the index) |
| 6 | Order management and execution simulation | P0 | Yes | **Done** (2026-11-23) |
| 7 | Portfolio management | P0 | Yes | **Done** (2026-11-23) |
| 8 | Performance analytics | P0 | Yes | **Done** (2026-11-23) |
| 9 | Reports and dashboard | P1 | Minimal static report only | **Minimal report done** (2026-11-23); Plotly charts and Streamlit app are post-MVP |
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
- **K15 (verified 2026-11-20, `corporate_actions` structure):** `type` and `caption` are empty. Action kind is encoded in `corpact_id`: 76 and 68 = bonus (ratio1 bonus shares per ratio2 held; price factor = ratio2/(ratio1+ratio2); e.g. Reliance 1:1 -> 0.5, BEL 2:1 -> 1/3, Powergrid 1:3 -> 0.75), 90 and 91 = split (ratio1:ratio2 = new face value:old face value; factor = ratio1/ratio2; e.g. Nestle 1:10 -> 0.1), 69 and 78 = rights (ratio1:ratio2 plus `offer_price`; needs a TERP factor, not yet checked), 77 = cash dividend (`amount` per share, `dividend_type`). Other ids (1, 31, 38, 61, 64, 65, 72, 73, 80) are announcements with no ex_date. For the Nifty 50 members, each bonus/split/rights event appears **twice** under two ids, so events must be deduplicated on (fin_code, ex_date, kind, ratios). Dates are varchar; ex_date is `YYYY-MM-DD 00:00:00.000`.
- **K16 (verified): the price series is only partly adjusted.** Reconciling event factors with price jumps at ex-dates: Bajaj Finserv (2022), BEL (2022), Powergrid (2023), Nestle (2024-01) show no jump (already adjusted by the data vendor). Reliance (2024-10-28, open/prev close 0.499), Wipro (2024-12-03, 0.498) and Shriram Finance (jump 0.233) show the full jump (not adjusted). Applying every action blindly would double-adjust the older ones. Adjustment must be decided per event by comparing the price ratio at the ex-date with the expected factor.
- **K17 (verified): `ex_date` can be wrong for splits.** Dr Reddy's split shows ex_date 2024-10-25, but prices are still ~6,583 on 2024-10-24 and ~1,200 on 2024-11-28 (about 1:5), so the real ex-date is within 2024-10-25..2024-11-28 and must come from the price series, not the table.
- **K18 (verified): NSE price history has holes.** Shriram Finance (111218) has only 2 NSE rows between 2024-11-28 and 2025-06-27; Dr Reddy has no NSE rows 2024-11-22..2024-11-27. Missing days cannot be filled by guessing; they must be reported, and symbols with large holes need a policy (exclude those periods, or fill from BSE rows if they validate).
- **K18 update (2026-11-20):** Measured NSE trading days per year vs the Nifty 50 index: 2018-2023 complete (about 244-252/yr, matching the index); 2024 about 168-175, 2025 about 139 (Shriram 72), 2026 about 54 (index 112) for Reliance/TCS/Shriram. **User will load the missing data.** Until then, the clean backtest window is 2018-2023. After the load, re-run the per-year coverage check, then resume Milestone 3. Ask whether the new rows are raw or adjusted and in the same table/format (`stock_type='NSE'`, ms `updated_time`).
- **K19 (verified 2026-11-20): NULL `open_price` blocks.** Reliance NSE has 64 rows with NULL open (59 in 2023-07-19..2023-10, 5 in 2024-10) but valid high/low/close/prev close/volume; TCS has 65 and Shriram 64, so it is likely common to all. Next-open fills need the open, so the normalizer rejects these rows (reason `null_price`) and they show up as missing days. This means 2018-2023 is NOT fully clean: about 60 missing days per stock in that window (mostly this block). Options: fix at source (user is adding data), or accept missing days. Do not fill the open with another price.
- **K20 (verified 2026-11-20): `corporate_actions` is incomplete for recent events.** Price jumps with no matching row: HDFCBANK about 0.499 on 2025-08-26 (1:1 bonus), NESTLEIND about 0.502 on 2025-08-08 (1:1 bonus), KOTAKBANK 0.200 on 2026-01-14 (split). The table has no split/bonus for HDFCBANK, Nestle after 2024-01-04, or Kotak at all; Kotak's last dividend is 2024-07-19. Also ADANIENT 0.711 on 2018-09-06 is unexplained. `find_unexplained_jumps` surfaces these; they must be added to the table (or resolved manually) before using those symbols across those dates. Correction to K17: Dr Reddy's jump is on 2024-10-31 (ratio 0.19), not late November; the listed ex-date 2024-10-25 is still wrong, by 6 days.
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

- **K21 (2026-11-23, updated): DP charge on sells is a Rs 20 placeholder (user decision; the user will confirm the real rate later).** zerodha.com/charges (fetched 2026-11-23) gave brokerage 0, STT 0.1% both sides, NSE transaction 0.00307%, SEBI Rs 10/crore, stamp 0.015% buy, GST 18% on brokerage+SEBI+transaction. The page text we could read did not show the DP charge, so the default schedule sets `dp_charge_per_sell` to 20 (config field, GST included, flat per sell fill). Replace it when the real rate is known.
- **K24 (2026-11-23): dividend coverage starts 2022-08.** The source table has no dividend rows before 2022-08-08 (13 in 2022, 77 in 2023, 78 in 2024, 20 in 2025, 1 in 2026 for the Nifty 50). Backtests before that date get no dividends, and 2025+ is probably incomplete (same gap as K20), so results before 2022-08 understate total return. Also: 11 (symbol, ex-date) pairs have two different amounts (for example interim plus special); both are paid. Index runs get no dividends (the Nifty 50 price index has none).
- **K22 (2026-11-23): current charge rates are applied to 2018-2026.** Rates changed over time (stamp duty, exchange charges). The config accepts dated schedules; only the current one is filled in. Results carry this approximation.
- **K23 (2026-11-23): execution simplifications.** Sale proceeds are usable the same day (no T+1), no volume cap or partial fills, no circuit-limit modelling, no tick rounding on adjusted prices.

## 4. Questions

Answered 2026-11-20:
- Q1 remote `univest` DB. Q2/corporate actions: `corporate_actions` is reliable (adjustment source). Q3 constituents: `index_companies_mapping` (current snapshot only); TRI still not located (see open). Q4 delivery only (capital still unset). Q5 long-only. Q6 approved (Python 3.12 venv, `src/`, SQLAlchemy+psycopg); dependencies tracked in `requirements.txt` (ADR-0009). Q7 new table for normalized bars. Q8 "use already one": no root `.gitignore` exists (only `.idea/.gitignore`), see open.

Open:
- O1: Nifty 50 **TRI** series source. User confirmed Nifty 50 `fin_code = 100256265` (2026-11-20). Verified this is the **price index** (close 25,758 on 2025-12-10; volume 0), not a TRI. Decision: benchmark = this series, labeled "Nifty 50 (price return)" in all reports until a TRI series is supplied. The dividend gap understates the benchmark by roughly 1.2-1.5%/yr [ASSUMPTION, not measured].
- O2: Where does the new bars table live? Proposal: local PostgreSQL (`/tmp:5432`, db `postgres`, new schema `algo`), because the remote DB is shared/production-style. Please confirm, or name the target.
- O3: Initial capital (default proposal: ₹10,00,000, configurable).
- O4: `.gitignore`: which existing file did you mean? Proposal: create a root `.gitignore` (venv, `.env`, `.idea/`, caches, `data/`) at scaffold time.
- O5: Member price history shorter than the backtest window (HDFCBANK NSE from 2025-06-18; JIOFIN 2023; ETERNAL 2021): how to treat? Proposal: use BSE rows or other `fin_code` history only if verified; otherwise the symbol enters the universe from its first valid NSE date.
- O6 (investigated, see K15-K17): the table does not fully support blind adjustment. Needs a decision, see ADR-0010 (Proposed) and the questions in chat.
- O7: Backtest date range (proposal: 2018-01-01 onward, subject to O5), and risk-free rate (proposal: configurable, default e.g. 6.5%/yr).

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
- Tasks: [x] project scaffold (requirements.txt, requirements-dev.txt, .venv on 3.12, pytest.ini; ruff deferred) [x] `.gitignore` [x] DB settings via env [x] `get_instruments(fin_codes)` [x] `get_index_members` [x] `get_raw_bars(fin_codes, start, end, stock_type)` [x] read-only connection [x] tests. Deviation: lookup is by fin_code (symbol lookup not needed yet); no ruff yet.
- Dependencies: M1; O2, O4.
- Deliverables: provider class + tests + README setup notes.
- Acceptance: returns raw rows for the Nifty 50 index and sample equities; no writes possible; no credentials in code; parameterized SQL only.
- Tests: unit (SQL building/param binding, config), integration against a temporary schema/fixture, error on unknown fin_code, empty range, DB down.
- Risks/edge cases: no indexes → slow scans (add a documented migration index only with approval); `numeric` → float conversion; timezone; unknown symbol; multiple fin_codes per symbol.

### Milestone 3: Data validation and normalization
- Objective: Turn raw rows into trustworthy canonical bars with a quality report.
- Scope: IST date derivation, dedupe, OHLC sanity (H≥max(O,C), L≤min(O,C), >0), NaN handling, gap detection against a trading calendar, volume handling, price-basis flag, fingerprint.
- Components: `data/validation.py`, `data/normalize.py`, `data/calendar.py`.
- Step 3a (DONE 2026-11-20): [x] date derivation (provider, IST) [x] OHLC validators [x] reject-with-reason policy [x] dedupe (latest `created_at`, seconds/ms normalized, bad rows cannot win) [x] calendar from the Nifty 50 index days [x] gap report [x] data fingerprint. Code: `data/normalize.py`; tests: `tests/unit/test_normalize.py` (11).
- Step 3b (DONE 2026-11-20): [x] `get_corporate_actions` in the provider [x] `data/adjust.py`: `build_events` (dedupe across ids, combine same-day events), `adjust_bars` (event reconciliation per ADR-0010, short-gap matching, no "already adjusted" verdict when a gap hides the window), `find_unexplained_jumps` [x] 13 unit tests. Real-data result for the Nifty 50 (2018-2026-09): 9 split/bonus events found; 2 applied (Reliance, Dr Reddy), 5 already adjusted by the vendor (BEL, Nestle 2024, Powergrid, Bajaj Finserv x2), 2 ambiguous because of long gaps (Wipro, Shriram). Not done: rights issues, dividends (kept as events for the portfolio), cross-check vs `last_day_close_price`.
- Step 3c (DONE 2026-11-23): [x] `algo` schema in local PostgreSQL (O2 as proposed): tables `bars` (adjusted OHLCV + `adj_factor`, PK `(fin_code, trading_date)`, OHLC CHECKs), `load_runs` (fingerprint, quality JSON), `adjustment_events`, `rejected_rows` [x] idempotent migration `data/migrations/001_create_bars.sql` [x] `data/store.py` (`BarStore`: migrate, `replace_bars` in one transaction, `read_bars`, `latest_run`) [x] `data/pipeline.py` (`load_bars`) and CLI `python -m algo_engine.data.load` (refuses to run if the store points at the source DB) [x] settings `StoreSettings` (`ALGO_PG*`, `ALGO_SCHEMA`) [x] 5 integration tests (throwaway schema `algo_test`) + 2 unit tests. Refresh replaces ALL stored bars of the symbols (adjustment depends on full history). First real load (run 1, Nifty 50 + index, 2018-01-01..2026-09-21): 84,554 bars, 3,174 adjusted; 2,779 rows rejected (2,697 null price), 2,523 duplicates removed, 12,843 missing symbol-days; events: 2 applied, 5 already adjusted, 2 ambiguous (gap); 4 unexplained jumps to review. [ ] optional Parquet cache (not built, no consumer yet).
- Dependencies: M2; O1, O5, O6.
- Deliverables: `normalize_bars()` + `DataQualityReport`.
- Acceptance: the 2 zero-OHLC rows and 66 duplicate pairs are detected and reported; output unique on `(fin_code, trading_date)`; deterministic fingerprint; no silent drops.
- Tests: duplicates with differing values, zero/NaN/negative prices, unsorted input, ms vs s timestamps, DST-free IST conversion, holiday vs missing day.
- Risks: duplicate resolution wrongly choosing stale data; unadjusted splits (see ADR-0005); calendar source.

### Milestone 4: Core event-driven engine
- Objective: Chronological loop wiring components with the event lifecycle in ARCHITECTURE.md §4.
- Scope: single timeframe (daily), 1..N symbols, synchronous, deterministic, event log, run directory output.
- Components: `engine/`, `events.py`, `config/RunConfig`.
- Tasks: [ ] event dataclasses [ ] loop & ordering rules [ ] HistoryView (truncated) [ ] run config + snapshot [ ] event log [ ] seed handling.
- Built (DONE 2026-11-23): [x] event dataclasses and `EventSink`/`EventLog` (hybrid: the loop owns ordering, components exchange immutable events through a sink; no pub/sub bus, ADR-0011) [x] loop and ordering rules (`engine/loop.py`) [x] `TruncatedHistory` [x] `RunConfig` from YAML or dict with JSON snapshot (`engine/config.py`) [x] run directory `runs/<run_id>/{config.json,events.jsonl,equity.csv,manifest.json}`, run id = hash(config + data fingerprint), no wall-clock values [x] look-ahead guard `find_lookahead`/`assert_no_lookahead` (`engine/guard.py`) [x] ports and no-op stubs (`engine/ports.py`: `Order`, `Fill`, `OrderManager`, `RiskManager`, `ExecutionSimulator`, `Portfolio`) [x] `noop` strategy [x] CLI `python -m algo_engine.engine.run --config configs/<file>.yaml` [x] 39 engine tests in `tests/unit/test_engine.py`. Seed is stored in the config and manifest; nothing consumes it yet (no random component exists). Real-data check: noop over Nifty 50 index 2018-2023, 1,484 days, byte-identical on rerun; EMA(12,26) gave 19 enter signals (the stub portfolio never holds, so no exits) and passed the guard at every 20th date.
- Dependencies: M3 output schema; interfaces from M5–M7 (stubs first).
- Acceptance: runs a no-op strategy over index data; identical output on rerun; no event at `t` references `t+1`.
- Tests: look-ahead truncation test, determinism, ordering (fill for orders created at `t` occurs ≥ `t+1`), multi-symbol calendar alignment.
- Risks: calendar misalignment across symbols; symbols with late listing/suspension; ordering ambiguity.

### Milestone 5: Strategy interface and indicator engine
- Objective: Plug-in strategies and causal indicators.
- Scope: `Strategy` ABC, `Signal`, param validation, strategy registry, indicators EMA, SMA, RSI, ATR, Bollinger, rolling returns (add as needed per strategy).
- Tasks: [x] interface (`strategy/base.py`: `Strategy`, `Signal`, `Bar`, `StrategyParams`, `HistoryView`/`PortfolioView` protocols) [x] registry/config loading (`strategy/registry.py`, `create_strategy({name, params})`) [x] indicators vs hand values (`indicators/core.py`: SMA, SMA-seeded EMA, Wilder RSI/ATR, Bollinger with population std, rolling returns; conventions in the module docstring) [x] causality tests (every indicator, plus a strategy truncation test and a test that a cheating strategy is flagged) [x] EMA crossover (`strategy/ema_crossover.py`, incremental, long-only). 35 tests, using a stub harness `tests/unit/_harness.py`. Open: run it through the real engine once M4 exists; `HistoryView`/`PortfolioView` are minimal and will be extended by M4/M7.
- Dependencies: M4 (parallel). Deliverables: EMA crossover running end to end.
- Acceptance: new strategy added with no engine edits; indicators match reference computations; warm-up handled explicitly (no signals before enough history).
- Tests: indicator unit tests, warm-up, causality, param validation, signal on exact crossover bar.
- Risks: pandas `ewm` seeding conventions (`adjust`); NaN warm-up; accidental `shift(-1)`.

### Milestone 6: Order management and execution simulation
- Objective: Orders with state machine; realistic fills with cost and slippage.
- Scope: MARKET orders, simple STOP; DAY validity; cost model; slippage; risk checks (cash, max position %, max open positions, lot/tick rounding).
- Tasks: [ ] Order/State machine [ ] signal→order sizing policy [ ] risk manager [ ] execution model [ ] cost model (rates in config) [ ] slippage model.
- Built (DONE 2026-11-23, ADR-0012): [x] order state machine (`engine/orderbook.py`, illegal transitions raise, terminal states final) [x] sizing (`orders/manager.py`: entry = floor(min(position_pct*equity, cash) / (close*(1+reserve_pct))), exit sells the whole position, unactionable signals logged as `SignalSkippedEvent`) [x] risk (`risk/manager.py`: cash with buffer and same-day commitments, max position %, max open positions, sells within the position; every rejection has a reason) [x] execution (`execution/simulator.py`: market at next open, STOP with gap-through at the open, sells before buys, cash re-checked at fill and refused rather than overdrawn) [x] costs (`execution/costs.py` + `CostSchedule` in `engine/params.py`, dated schedules, per-fill breakdown logged in `FillEvent`) [x] slippage (`FixedBpsSlippage`, default 5 bps, also applied to triggered stops) [x] config sections `sizing`, `risk`, `costs`, `slippage_bps`; `build_components(config)` in `engine/build.py` [x] 49 tests in `tests/unit/test_m6.py` (hand-calculated costs, stop semantics, state machine, a wired run with a test ledger that checks cash never negative, fills after signals, cost and cash reconciliation, byte-identical reruns). Not done: automatic stop placement (`stop_loss_pct`), tick rounding (skipped on purpose, prices are adjusted), volume cap, T+1 settlement, partial fills. `run_backtest` still defaults to no-op stubs because the stub portfolio cannot apply fills; pass `build_components(config)` once M7 exists, then make it the default.
- Dependencies: M4, M5, K-data on tick/lot. Acceptance: signal at `t` never fills before `t+1`; costs reconcile; rejected orders logged with reason.
- Tests: gap-through stop, stop & target same bar, insufficient cash, zero quantity, cost breakdown against hand calc, order state transitions.
- Risks: wrong STT/charge rates; limit-price semantics; unfillable orders at limit-up/down (circuit) not modeled.

### Milestone 7: Portfolio management
- Objective: Correct accounting.
- Tasks: [x] cash/positions [x] avg-cost P&L [x] mark-to-market [x] equity curve (loop, `equity.csv`) [x] trade log (round trips, `trades.csv`) [x] dividends (below) [ ] other corporate-action hooks (not needed: splits and bonuses are already in the adjusted prices).
- Built (DONE 2026-11-23): `portfolio/manager.py`, `AverageCostPortfolio` and `Trade`. Conventions are in the module docstring: buy cash = qty*price + costs, sell cash = qty*price - costs, average price is gross (costs are charged to the trade, not folded into the average), one `Trade` per round trip (partial sells stay inside it), fills that would overdraw cash or sell more than held raise `ExecutionError`. `run_backtest` and the CLI now default to `build_components` plus this portfolio; pass the no-op stubs from `engine.ports` to run without trading. `RunResult.trades` and `trades.csv` added; the CLI prints trades and final equity. 6 tests in `tests/unit/test_portfolio.py` (hand calculation, partial close, re-entry, invalid fills, 25 random fill sequences checking cash >= 0, equity = cash + positions, and equity - capital = realized net + open P&L - open costs, plus a default end-to-end run with byte-identical reruns). Real-data check: EMA(12,26) on Nifty 50 index 2018-2023: 1,484 days, 37 signals, 18 closed trades, final equity 1,944,036 from 1,000,000 with the last position still open; first buy (93 units, 2018-04-13) matched a hand calculation of quantity, slippage and costs; rerun byte-identical. Not done: T+1, drawdown and other metrics (M8).
- Dividends (DONE 2026-11-23, user request): `corpact_id` 77 rows become cash. Data: `data/dividends.py` (`build_dividends`: positive amounts only, exact duplicates on (fin_code, ex_date, amount) removed, different amounts on one ex-date kept, amount scaled to the adjusted series as `amount * adj_factor` at the ex-date), table `algo.dividends` (migration `002_create_dividends.sql`, migrations now run in file order), `BarStore.read_dividends/replace_dividends`, `load_bars` fills it, and `python -m algo_engine.data.load --dividends-only` rebuilds only dividends from the stored bars. Engine: `run_backtest(..., dividends=)`, `DividendEvent`, `Portfolio.apply_dividend`, `Trade.dividends` (`net_pnl = gross - costs + dividends`), `trades.csv` column, manifest `dividends_credited` and `dividends_fingerprint` (the dividends are part of the run id). Rule: at the start of each trading day, every dividend with an ex-date up to that day is paid on the shares held at the previous close, before that day's fills. So a buy filled on the ex-date is not paid, a sell filled on the ex-date is, and an ex-date on a non-trading day is paid on the next trading day. Cash is credited on the ex-date (real payment comes weeks later); no tax or TDS. Tests: 7 new (builder, portfolio, loop entitlement, store round trip). Real check: Reliance 2022-2023 EMA(12,26) got one dividend, 726 shares x Rs 4.00 (Rs 8 raw x 0.5 for the 2024 bonus) = Rs 2,904, on 2022-08-18 while held 2022-08-03..2022-09-19; reruns byte-identical. Loaded: 186 dividends, 47 symbols, 2022-08-08..2026-07-15.
- Acceptance: invariants and reconciliation hold on randomized fill sequences.
- Tests: property tests, partial close, re-entry, fees, dividends/splits (when implemented).
- Risks: rounding drift; partial-position cost basis; costs counted twice.

### Milestone 8: Performance analytics
- Objective: Metrics in ARCHITECTURE.md §8, benchmark comparison.
- Tasks: [x] metrics module [x] benchmark loader (Nifty 50 price index now; TRI when available) [x] trade stats [x] bootstrap CIs.
- Built (DONE 2026-11-23): `analytics/metrics.py` (pure functions, formulas as in ARCHITECTURE section 8: total return, CAGR, volatility, Sharpe, Sortino, Calmar, max drawdown with peak/trough/recovery dates and longest time under water, exposure, beta/alpha/correlation/tracking error/information ratio, trade stats, seeded percentile bootstrap), `analytics/report.py` (`load_run`, `analyze`, `write_metrics`, `benchmark_series`, `format_summary`) and CLI `python -m algo_engine.analytics.run --run-dir runs/<id> [--rf 0.065] [--benchmark 100256265 | --no-benchmark] [--bootstrap 2000]`, which writes `metrics.json` into the run directory (deterministic bytes). Risk-free rate default 6.5%/yr (O7 proposal), passed to the analytics, not part of `RunConfig`. Undefined values are null in JSON (zero volatility, no trades, under two points, no downside), profit factor with no losses is "inf". Warnings always listed: fewer than 30 trades, history under one year, i.i.d. bootstrap, open positions excluded from trade stats, benchmark is a price index. Bootstrap uses the run's `seed`. 14 tests in `tests/unit/test_analytics.py` (hand-computed Sharpe/Sortino/CAGR/drawdown, known beta and alpha recovered, edge cases, seeded bootstrap, full run directory). Real check on the 2018-2023 index run (EMA 12/26): total return 94.4% (Nifty 50 price index 108.2%), Sharpe 0.53 at 6.5% rf (95% interval -0.27 to 1.33), max drawdown -11.7% vs -38.4%, beta 0.32, 18 trades, 50% win rate; all matched an independent pandas recomputation.
- Limits: the Sharpe interval treats daily returns as independent (no block bootstrap); the benchmark is a price index (understates a TRI by roughly the dividend yield); equity-basket comparisons wait for the 2024+ data (K18).
- Dependencies: M7, K3. Acceptance: metrics match hand-computed fixtures; edge cases defined.
- Tests: flat equity, single trade, no losing trades (profit factor), zero-volatility Sharpe, short history, all-negative returns.
- Risks: annualization conventions; rf rate choice; benchmark date alignment.

### Milestone 9: Reports and dashboard
- Tasks: [x] static HTML report with equity, drawdown, trades, metrics, config, data fingerprint and caveats [ ] Plotly charts (not needed, the report uses inline SVG, no new dependency) [ ] Streamlit app (post-MVP).
- Built (DONE 2026-11-23): `reports/static.py` (`build_report`, `caveats`, SVG `line_chart`) and CLI `python -m algo_engine.reports.build --run-dir runs/<id>`; `python -m algo_engine.analytics.run --run-dir runs/<id> --report` scores and builds in one go. The report reads only the run directory (`config.json`, `manifest.json`, `equity.csv`, `trades.csv`, `metrics.json`, and `benchmark.csv`, which the analytics step now saves), so it can be rebuilt and is byte-identical for the same files. Sections: caveats first, setup, performance table (strategy vs benchmark, relative metrics), equity chart with the rebased benchmark, drawdown chart, trade statistics with bootstrap intervals, trade table (first 500), reproducibility (run id, bar and dividend fingerprints, seeds) and the config. Caveats always printed: survivorship (current constituents), in-sample, costs (current rates, slippage, DP placeholder, no T+1), dividends (none before 2022-08), data adjustment limits; plus benchmark label or the warning that no benchmark is included, a ZERO-cost diagnostic warning, an index-instrument warning and the metric warnings. All file values are HTML-escaped. 8 tests in `tests/unit/test_reports.py`. Visually checked the charts by rendering the SVG for the 2018-2023 index run.
- Acceptance: report reproducible from run directory; caveats (survivorship, in-sample, costs) are always printed.

### Milestone 10: Optimization and robustness
- Tasks: [ ] parameter grid runner with run registry [ ] walk-forward and train/test split [ ] parameter-sensitivity heatmaps [ ] Monte Carlo/bootstrapped trade shuffles [ ] multiple-testing correction (e.g. deflated Sharpe) [ ] cost/slippage stress tests [ ] remaining roadmap strategies.
- Acceptance: results show out-of-sample vs in-sample and number of trials.

### Milestone 11: Paper trading
- Tasks: [ ] live/near-live data provider [ ] `PaperBroker` [ ] scheduler/runner [ ] state persistence [ ] reconciliation vs backtest. Dependencies: M4–M8, Kite market data decisions.

### Milestone 12: Zerodha Kite integration
- Tasks: [ ] auth flow (daily token) [ ] instrument mapping via `kite_trading_symbol` [ ] `KiteBroker` [ ] order safeguards (kill switch, max order value, dry-run default) [ ] audit log. Dependencies: M11, Kite developer subscription. Risks: real money; secrets; rate limits; static IP requirements [ASSUMPTION, verify current Kite rules].

## 6. Completed Tasks

- 2026-11-20: Milestone 3a. `data/normalize.py` (normalize_bars, DataQualityReport, build_calendar, find_missing_dates, bars_fingerprint), 11 new unit tests, 23 unit tests total pass. Real-data run (index + Reliance, TCS, Shriram, 2018-2026-09): index rows_in 2156 -> out 2071 (7 rejected, 78 duplicates removed); stocks rows_in 5650 -> out 5297 (202 rejected: 193 null open, 9 inconsistent OHLC; 151 duplicates removed; 3 conflicting duplicate dates). Fingerprint independent of row order.
- 2026-11-20: Milestone 2. Files: `src/algo_engine/config/settings.py`, `data/provider.py`, `data/postgres_provider.py`; tests `tests/unit/` (12) and `tests/integration/` (5, live, skipped without credentials). All 17 passed. Raw bars query filters by fin_code + IST date, NSE default, cast to float, ordered, undeduplicated. Index members parsed from `index_companies_mapping`. Writes rejected (verified by test). Remote queries are fast with the fin_code index.
- 2026-11-20: Repository and database inspection (read-only); four documentation files created. No application code or DB changes.

## 7. Next Recommended Tasks

(2026-11-23, after M9) 1. Run a Nifty 50 stock basket once the 2024+ data is loaded (K18), then re-run the per-year coverage check. 2. M10: parameter grid runner with a run registry, walk-forward and train/test split, cost and slippage stress tests, multiple-testing correction. 3. Cost follow-ups: confirm the DP charge on sells (K21, Rs 20 placeholder) and decide whether to add historical schedules (K22). 4. Close M3 items (K18, K19, ambiguous Wipro/Shriram, 4 unexplained jumps). 5. O3 capital default stays 1,000,000 unless you change it.

(2026-11-23) User chose to build Milestone 5 before M4. M5 code is done; next is M4 (needs O3 capital and O7 date range), with M6/M7 interfaces as needed.

0. Milestone 3 is paused until the user loads the missing 2024+ data (K18) and decides the decision on ADR-0010 (adjustment approach) and the data-gap policy (K18).
1. Milestone 3c: bars table. Also decide how to treat K20 (missing recent actions) and the ambiguous events (Wipro, Shriram).
2. (old text) corporate-action adjustment from `corporate_actions` (first inspect its `type`/`ex_date` values and assert adjusted series against `last_day_close_price`), trading calendar from the data itself (the union of Nifty 50 index and member dates), new `algo` schema table for bars (O2, needs user OK), data fingerprint.
2. User: confirm O2 (bars table location), O3 (capital), O5, O7.
2. Milestone 2, task 1 (proposed first task): scaffold the project (`requirements.txt`, venv on Python 3.12, `.gitignore`, pytest) and implement a read-only `PostgresMarketDataProvider` with `get_raw_bars` and `get_instruments`, tested against the remote DB read-only (Nifty 50 index plus the seven large caps). No engine work yet.
3. Then Milestone 3 normalization with a deterministic data-quality report.
