# Architecture

Status: **design, with the data provider implemented (see section 10).** Everything below is the target design (Proposed) except where marked [VERIFIED]. Update this file in the same change as any implementation; when code and this file disagree, fix this file.

Last updated: 2026-11-20.

## 1. System Overview

```
 PostgreSQL (read-only)         Config (YAML)
   company_master                     |
   stock_prices_five_years            v
        |                       +-----------+
        v                       | RunConfig |
 +----------------+             +-----------+
 | MarketData     |  raw rows        |
 | Provider       |------> +---------------------+   canonical bars
 +----------------+        | Validation &        |-------------------+
                           | Normalization       |                   |
                           +---------------------+                   v
                                                         +---------------------+
                                                         |   Backtest Engine   |
                                                         |   (event loop)      |
                                                         +---------------------+
   per bar t:  MarketEvent -> Indicators -> Strategy -> Signal -> OrderManager
               -> Risk -> ExecutionSimulator -> Fill -> Portfolio -> Valuation
                                                         |
                                  event log + equity curve + trades
                                                         v
                                        Performance Analytics -> Reporting
```

The same strategy/order/risk/portfolio code must later run under paper and live runners. Only the **data source** and **execution venue** change (see §7).

## 2. Component Responsibilities

| Component | Responsibility | Must NOT |
|---|---|---|
| Historical Data Provider | Fetch raw bars for symbols/date range from a source (Postgres first). Return a canonical DataFrame. | Compute indicators; apply strategy logic; write to source tables |
| Data Validation & Normalization | Derive `trading_date`, dedupe, check OHLC consistency, gaps, zero/NaN, units; produce a `DataQualityReport`; apply price basis | Silently drop or repair rows |
| Indicator Engine | Compute indicators (EMA, RSI, ATR, ...) causally from bars ≤ t | Peek forward; hold strategy state |
| Strategy Engine | Host strategy instances; call `on_bar`; collect signals | Create fills; touch portfolio cash |
| Signal Generation | Strategy output: intent (symbol, direction, target/strength, reason) | Size by cash or send orders |
| Order Management | Turn signals into orders (sizing policy), track order state machine | Decide fills |
| Risk Management | Pre-trade checks and position limits; may reject or resize | Mutate portfolio |
| Execution Simulator | Decide if/at what price/qty an order fills in a bar; apply slippage and costs | Know about strategies |
| Portfolio Management | Cash, positions, cost basis, realized/unrealized P&L, equity, applies fills and corporate actions | Generate signals |
| Performance Analytics | Metrics and benchmark comparison from equity curve and trade log | Re-run the simulation |
| Reporting | Tables, Plotly charts, run summaries (Streamlit later) | Compute metrics itself |

## 3. Data Flow

1. `RunConfig` selects symbols, date range, price basis, strategy + params, cost model, slippage model, risk limits, initial capital, seed.
2. Provider returns raw rows. Validator produces canonical bars plus `DataQualityReport`. Run aborts on blocking issues (configurable severity).
3. Engine builds a chronological iterator over a **union trading calendar** of the selected symbols (single symbol initially).
4. Each step emits events (below). Results (equity curve, orders, fills, trades, event log, config snapshot, data fingerprint) are written to a run directory for reproducibility.

### Canonical bar schema (target)

`trading_date (date, IST), fin_code, symbol, open, high, low, close, volume, price_basis, source_row_count`. Index: `(fin_code, trading_date)` unique. Prices float64, volume float64/int, no NaN in OHLC after validation.

### Source schema [VERIFIED 2026-11-20, local DB `postgres`, schema `public`]

`stock_prices_five_years(fin_code bigint, stock_type varchar, open_price, close_price, high_price, low_price, volume_traded numeric, updated_time bigint [epoch ms], created_at numeric [mixed s/ms], last_day_close_price numeric, oi numeric)`. No PK, constraints or indexes. `company_master(fin_code bigint PK, symbol, scrip_name, comp_name, isin, kite_trading_symbol, segment, instrument_type, deleted, market_capital, sector, industry, tick_size, lot_size, ... 40 columns)`. Join: `fin_code`.

## 4. Event Lifecycle

Per trading date `t` the engine does, in this order:

1. **Start of bar `t` (open phase):** pending orders created on earlier bars are submitted to the Execution Simulator, which tries to fill them using bar `t` OHLC (market → open; stops/limits per rules). Fills go to the Portfolio.
2. **Close of bar `t`:** `MarketEvent(t)` is published. Portfolio is marked to market at close `t` (valuation).
3. Indicators are updated with bar `t` (causal).
4. Strategy `on_bar(t, history_view≤t, portfolio_view)` returns zero or more `Signal`s.
5. Order Manager converts signals to `Order`s (state `CREATED`); Risk validates (`ACCEPTED` or `REJECTED`). Accepted orders wait for bar `t+1` (step 1 of the next iteration).
6. Metrics and event log recorded for `t`.

Rationale: signals formed on close `t` can only fill from `t+1`. Equity at the end of bar `t` includes fills made at bar `t`'s open and mark-to-market at close `t`.

Event types (target): `MarketEvent`, `SignalEvent`, `OrderEvent`, `FillEvent`, `PortfolioUpdateEvent`, `RiskRejectionEvent`, `DataQualityEvent`. Events are immutable dataclasses with a monotonic sequence id and `trading_date`. The engine, not components, decides ordering.

Internally the engine may be a simple synchronous loop that calls components directly and logs events. A full publish/subscribe bus is not required for the MVP (see Trade-offs).

## 5. Order Lifecycle

```
CREATED -> (risk) -> REJECTED
        -> ACCEPTED/PENDING -> (bar t+1+) -> PARTIALLY_FILLED* -> FILLED
                            -> CANCELLED (strategy/expiry)
                            -> EXPIRED (day order not filled)
```
\*Partial fills are not in the MVP (full-fill assumption, with a volume participation cap added later).

Order fields: `order_id, symbol/fin_code, side, quantity, order_type (MARKET, later LIMIT/STOP), limit/stop price, time_in_force (DAY default), created_on (trading_date), source signal id, strategy id`. Terminal states are immutable. All transitions are logged.

### Fill model (daily bars)

Defined in CLAUDE.md §6.3 and ADR-0006. Summary: market → next open ± slippage; stop gap-through → fill at open; stop & target in same bar → stop first.

### Cost model (target; rates configurable with effective dates)

Per fill: brokerage, STT (on delivery both sides / intraday sell side), exchange transaction charge, SEBI turnover fee, stamp duty (buy), GST on brokerage + fees, plus slippage (bps of price, optionally volume/ATR-based). **Exact current NSE/Zerodha rates must be sourced and recorded in the config when implemented; they are not hardcoded in this document** [ASSUMPTION: delivery equity, CNC product first].

## 6. Portfolio Lifecycle

1. Init: `initial_cash`.
2. Apply fill: BUY decreases cash by `qty*price + costs`, increases position, updates average cost. SELL increases cash by `qty*price − costs`, reduces position, books realized P&L = `(price − avg_cost) * qty_closed` (costs tracked separately, reported both gross and net).
3. Mark-to-market each close: `unrealized = (close − avg_cost) * qty`; `equity = cash + Σ qty*close`.
4. Corporate action (when supported): split/bonus adjust qty and cost basis; dividend credits cash on the ex-date entitlement (documented convention).
5. Invariants (asserted in tests): cash ≥ 0 (cash-only mode); `equity_t = initial_cash + realized_net + unrealized − …` reconciles; position quantity equals signed sum of fills; no short selling in MVP (long-only) [ASSUMPTION, see open question Q5].
6. Output: equity curve (date, cash, positions value, equity), trade log (round trips: entry/exit date, qty, prices, costs, P&L, holding days), position snapshots.

## 7. Important Interfaces (target Python signatures; Protocol/ABC)

```python
class MarketDataProvider(Protocol):
    def get_bars(self, fin_codes: Sequence[int], start: date, end: date) -> pd.DataFrame: ...
    def get_universe(self, as_of: date) -> Sequence[Instrument]: ...

class Strategy(ABC):
    params: StrategyParams            # validated, from config
    def on_start(self, ctx: StrategyContext) -> None: ...
    def on_bar(self, bar: Bar, history: HistoryView, portfolio: PortfolioView) -> list[Signal]: ...
    # HistoryView only exposes data <= bar.trading_date

class Indicator(Protocol):             # causal; value at t depends only on <= t
    def compute(self, bars: pd.DataFrame) -> pd.Series | pd.DataFrame: ...

class RiskManager(Protocol):
    def validate(self, order: Order, portfolio: PortfolioView, bar: Bar) -> RiskDecision: ...

class ExecutionModel(Protocol):        # backtest simulator; paper/live replace the venue
    def try_fill(self, order: Order, bar: Bar) -> Fill | None: ...

class CostModel(Protocol):
    def cost(self, side: Side, qty: int, price: float) -> CostBreakdown: ...

class SlippageModel(Protocol):
    def adjust(self, side: Side, price: float, bar: Bar) -> float: ...

class Broker(Protocol):                # later: SimulatedBroker, PaperBroker, KiteBroker
    def submit(self, order: Order) -> None: ...
    def cancel(self, order_id: str) -> None: ...
```

Paper/live parity: `Broker` and a live `MarketDataProvider` swap in; `Strategy`, `OrderManager`, `RiskManager`, `Portfolio` are reused unchanged.

**Look-ahead guard:** `HistoryView` is a read-only slice object constructed by the engine per bar. Indicators may be precomputed vectorized on the full series only if they are causal (verified by the truncation test in CLAUDE.md §9); otherwise computed incrementally.

## 8. Performance Analytics (formulas; implement exactly)

Inputs: daily equity series `E_t`, daily returns `r_t = E_t/E_{t−1} − 1`, `rf` annual (configurable), `N = 252`.

- Total return = `E_T/E_0 − 1`.
- CAGR = `(E_T/E_0)^(365.25/days) − 1`, `days` = calendar days between first and last date.
- Sharpe = `mean(r_t − rf/N) / std(r_t, ddof=1) * sqrt(N)`.
- Sortino = `mean(r_t − rf/N) / DD * sqrt(N)`, `DD = sqrt(mean(min(r_t − rf/N, 0)^2))` over all periods.
- Max drawdown = `min_t (E_t / max_{s≤t} E_s − 1)`; also report duration.
- Win rate = winning round-trip trades / total closed trades (net of costs).
- Profit factor = gross profit / |gross loss| of closed trades (net of costs); undefined (report `inf`/NaN explicitly) when no losses.
- Average holding period = mean calendar days (and trading days) of closed trades.
- Number of trades = closed round trips (also report total fills).
- Exposure = time-weighted mean of `position market value / equity`; also % of days with any position.
- Benchmark: same metrics on the benchmark series, plus alpha/beta (OLS on daily excess returns), tracking error, information ratio. Benchmark = Nifty 50 TRI [data not yet available; see Known Issue K3].
- Statistical caution: report trade count and confidence intervals (bootstrap) for Sharpe/mean trade return; flag low-sample results.

## 9. Architectural Decisions and Trade-offs

See `docs/DECISIONS.md` for the full records. Summary:

- Event-driven custom engine (ADR-0001): fidelity and live parity at the price of more code and slower sweeps.
- Synchronous loop with logged events rather than a message bus (ADR-0011): the loop owns phase ordering, components exchange immutable events through an `EventSink` (`engine/events.py`). A bus can be added later as another sink. Code: `src/algo_engine/engine/` (`loop.py` order of phases, `ports.py` interfaces for M6/M7, `guard.py` look-ahead check). Run output: `runs/<run_id>/{config.json, events.jsonl, equity.csv, manifest.json}`. Missing bars inside a symbol's history raise a `DataQualityEvent` and the symbol is valued at its last close; the strategy is not called for it.
- Signals and orders are separate objects: strategies express intent; sizing, risk and execution remain replaceable.
- Normalized bar layer between raw table and engine (ADR-0004).
- Fills at next open (ADR-0006).
- Long-only, cash-account, delivery-style MVP: avoids short borrow, margin and intraday-product rules until needed.

## 10. Implementation Status

| Component | Status |
|---|---|
| Config (`config/settings.py`: `DbSettings` from env, read-only session options) | Implemented |
| Historical Data Provider (`data/provider.py` interface, `data/postgres_provider.py`) | Implemented: `get_instruments`, `get_index_members` (parsing of `companies` text), `get_raw_bars` (raw, not cleaned) |
| Data Validation & Normalization (`data/normalize.py`) | Implemented (3a): reject-with-reason, dedupe, calendar, gaps, fingerprint. Bars table (3c) implemented in `data/store.py`, `data/pipeline.py`, `data/load.py` |
| Price adjustment (`data/adjust.py`) | Implemented (3b): event-reconciled split/bonus adjustment, unexplained-jump check. Rights and dividends not price-adjusted |
| Everything else | Not started |
