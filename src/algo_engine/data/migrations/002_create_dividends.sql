-- Cash dividends (Milestone 7 follow-up). Idempotent. {schema} is substituted by BarStore.
-- amount is per raw share as declared, adj_amount = amount * adj_factor is per share of the ADJUSTED bar series.
CREATE TABLE IF NOT EXISTS {schema}.dividends (
    fin_code      bigint NOT NULL,
    ex_date       date NOT NULL,
    amount        double precision NOT NULL,
    adj_factor    double precision NOT NULL,
    adj_amount    double precision NOT NULL,
    dividend_type text,
    loaded_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fin_code, ex_date, amount),
    CONSTRAINT dividends_positive CHECK (amount > 0 AND adj_factor > 0 AND adj_amount > 0)
)
