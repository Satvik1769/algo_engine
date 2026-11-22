-- Normalized, split/bonus-adjusted daily bars (Milestone 3c). Idempotent. {schema} is substituted by BarStore.
-- Prices in algo.bars are ADJUSTED (see adj_factor). Raw price = price / adj_factor, raw volume = volume * adj_factor.
CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.load_runs (
    run_id        bigserial PRIMARY KEY,
    loaded_at     timestamptz NOT NULL DEFAULT now(),
    start_date    date NOT NULL,
    end_date      date NOT NULL,
    fin_codes     bigint[] NOT NULL,
    calendar_code bigint NOT NULL,
    fingerprint   text NOT NULL,
    rows_loaded   integer NOT NULL,
    quality       jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS {schema}.bars (
    fin_code     bigint NOT NULL,
    trading_date date NOT NULL,
    open         double precision NOT NULL,
    high         double precision NOT NULL,
    low          double precision NOT NULL,
    close        double precision NOT NULL,
    volume       double precision,
    prev_close   double precision,
    adj_factor   double precision NOT NULL DEFAULT 1,
    run_id       bigint NOT NULL REFERENCES {schema}.load_runs (run_id),
    PRIMARY KEY (fin_code, trading_date),
    CONSTRAINT bars_ohlc_valid CHECK (
        open > 0 AND high > 0 AND low > 0 AND close > 0
        AND high >= low AND high >= open AND high >= close AND low <= open AND low <= close),
    CONSTRAINT bars_adj_factor_positive CHECK (adj_factor > 0)
);

CREATE TABLE IF NOT EXISTS {schema}.adjustment_events (
    run_id         bigint NOT NULL REFERENCES {schema}.load_runs (run_id),
    fin_code       bigint NOT NULL,
    ex_date        date NOT NULL,
    kind           text NOT NULL,
    factor         double precision,
    status         text NOT NULL,
    apply_date     date,
    observed_ratio double precision,
    note           text NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS {schema}.rejected_rows (
    run_id       bigint NOT NULL REFERENCES {schema}.load_runs (run_id),
    fin_code     bigint NOT NULL,
    trading_date date,
    reason       text NOT NULL,
    open         double precision,
    high         double precision,
    low          double precision,
    close        double precision
);

CREATE INDEX IF NOT EXISTS adjustment_events_run_idx ON {schema}.adjustment_events (run_id);
CREATE INDEX IF NOT EXISTS rejected_rows_run_idx ON {schema}.rejected_rows (run_id);
