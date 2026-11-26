"""Building the dividends table from corporate-action rows."""
from __future__ import annotations

from datetime import date

import pandas as pd

from algo_engine.data.dividends import build_dividends


def actions(rows):
    return pd.DataFrame(rows, columns=["fin_code", "corpact_id", "ex_date", "amount", "dividend_type"])


def bars(code=1, factor_before=0.5, split_day=date(2024, 3, 1)):
    days = list(pd.bdate_range("2024-01-01", "2024-06-28").date)
    return pd.DataFrame({"fin_code": code, "trading_date": days,
                         "adj_factor": [factor_before if d < split_day else 1.0 for d in days]})


def test_filters_dedupes_and_scales_to_the_adjusted_series():
    a = actions([
        (1, 77, date(2024, 2, 5), 10.0, "Final"),
        (1, 77, date(2024, 2, 5), 10.0, "Final"),      # exact duplicate
        (1, 77, date(2024, 2, 5), 3.0, "Special"),     # same day, other amount: kept
        (1, 77, date(2024, 5, 6), 4.0, "Interim"),     # after the 1:1 bonus, factor 1
        (1, 76, date(2024, 2, 6), 1.0, None),          # a bonus, not a dividend
        (1, 77, date(2024, 2, 7), None, "Interim"),    # no amount
        (1, 77, date(2024, 2, 8), 0.0, "Interim"),     # zero amount
        (1, 77, date(2023, 12, 1), 5.0, "Final"),      # before the first bar: dropped
        (1, 77, date(2024, 9, 1), 5.0, "Final"),       # after the last bar: dropped
        (2, 77, date(2024, 2, 5), 5.0, "Final"),       # symbol without bars: dropped
    ])
    out = build_dividends(a, bars())
    assert list(out["ex_date"]) == [date(2024, 2, 5), date(2024, 2, 5), date(2024, 5, 6)]
    assert list(out["amount"]) == [3.0, 10.0, 4.0]
    assert list(out["adj_amount"]) == [1.5, 5.0, 4.0]           # raw 10 per share = 5 per adjusted share


def test_ex_date_on_a_non_trading_day_uses_the_previous_bar_factor():
    out = build_dividends(actions([(1, 77, date(2024, 2, 10), 8.0, "Final")]), bars())   # Saturday
    assert list(out["adj_amount"]) == [4.0]


def test_empty_inputs():
    assert build_dividends(actions([]), bars()).empty
    assert build_dividends(actions([(1, 76, date(2024, 2, 5), 1.0, None)]), bars()).empty
