"""Cash dividends from `corporate_actions` (corpact_id 77), scaled to the adjusted price series.

Bars are adjusted for splits and bonuses (adjusted price = raw price * adj_factor). A backtest holds adjusted
shares, so a dividend of `amount` per raw share on `ex_date` is `amount * adj_factor(ex_date)` per adjusted share.
`adj_factor` is taken from the last stored bar on or before the ex-date.

Rules: only rows with corpact_id 77, a positive amount and an ex-date; exact duplicates on
(fin_code, ex_date, amount) are removed; different amounts on one ex-date (for example interim plus special)
are kept as separate dividends. Dividends with no bar on or before the ex-date, or after the symbol's last bar,
are dropped. Coverage is whatever the source table holds (the Nifty 50 rows start in 2022-08).
"""
from __future__ import annotations

import pandas as pd

DIVIDEND_ACTION_ID = 77
DIVIDEND_COLUMNS = ["fin_code", "ex_date", "amount", "adj_factor", "adj_amount", "dividend_type"]


def build_dividends(actions: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    """`actions`: provider corporate-action rows. `bars`: adjusted bars with `adj_factor`."""
    empty = pd.DataFrame(columns=DIVIDEND_COLUMNS)
    if actions.empty or bars.empty:
        return empty
    d = actions[actions["corpact_id"] == DIVIDEND_ACTION_ID]
    d = d[d["amount"].notna() & (d["amount"] > 0) & d["ex_date"].notna()]
    d = d.drop_duplicates(["fin_code", "ex_date", "amount"])
    if d.empty:
        return empty
    left = d[["fin_code", "ex_date", "amount", "dividend_type"]].copy()
    left["fin_code"] = left["fin_code"].astype("int64")
    left["ex_ts"] = pd.to_datetime(left["ex_date"])
    right = bars[["fin_code", "trading_date", "adj_factor"]].copy()
    right["fin_code"] = right["fin_code"].astype("int64")
    right["bar_ts"] = pd.to_datetime(right["trading_date"])
    last_bar = right.groupby("fin_code")["bar_ts"].max().rename("last_ts")
    merged = pd.merge_asof(left.sort_values("ex_ts"), right.sort_values("bar_ts")[["fin_code", "bar_ts", "adj_factor"]],
                           left_on="ex_ts", right_on="bar_ts", by="fin_code", direction="backward")
    merged = merged.join(last_bar, on="fin_code")
    merged = merged[merged["adj_factor"].notna() & (merged["ex_ts"] <= merged["last_ts"])].copy()
    merged["adj_amount"] = merged["amount"] * merged["adj_factor"]
    out = merged.sort_values(["fin_code", "ex_date", "amount"]).reset_index(drop=True)
    return out[DIVIDEND_COLUMNS]
