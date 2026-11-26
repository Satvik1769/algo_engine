from datetime import date

import numpy as np
import pandas as pd

from algo_engine.data.normalize import (
    bars_fingerprint, build_calendar, build_trading_calendar, find_missing_dates, normalize_bars,
)

COLS = ["fin_code", "stock_type", "updated_time", "created_at", "trading_date", "open_price",
        "high_price", "low_price", "close_price", "volume_traded", "last_day_close_price"]


def row(day, o=100.0, h=110.0, l=95.0, c=105.0, created=1_700_000_000_000.0, fin_code=1, v=1000.0):
    return [fin_code, "NSE", 0, created, day, o, h, l, c, v, 99.0]


def raw(*rows):
    return pd.DataFrame(list(rows), columns=COLS)


D1, D2, D3 = date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)


def test_clean_rows_pass_through_sorted():
    bars, rep = normalize_bars(raw(row(D2), row(D1)))
    assert list(bars["trading_date"]) == [D1, D2]
    assert rep.rows_in == 2 and rep.rows_out == 2 and rep.rejected.empty


def test_rejects_with_reasons_and_does_not_repair():
    bad = raw(
        row(D1, o=0.0, h=0.0, l=0.0),               # zero prices
        row(D2, o=np.nan),                          # null open
        row(D3, h=90.0),                            # high below low
    )
    bars, rep = normalize_bars(bad)
    assert bars.empty
    assert rep.reject_counts() == {"non_positive_price": 1, "null_price": 1, "high_below_low": 1}


def test_ohlc_inconsistency_rejected():
    _, rep = normalize_bars(raw(row(D1, o=120.0), row(D2, l=106.0)))
    assert rep.reject_counts() == {"high_below_open_or_close": 1, "low_above_open_or_close": 1}


def test_duplicates_keep_latest_created_at_even_when_units_differ():
    older_sec = row(D1, c=101.0, created=1_700_000_000.0)       # seconds
    newer_ms = row(D1, c=102.0, created=1_700_000_500_000.0)    # milliseconds, later
    bars, rep = normalize_bars(raw(newer_ms, older_sec))
    assert bars["close"].tolist() == [102.0]
    assert rep.duplicates_removed == 1 and rep.conflicting_duplicate_dates == 1


def test_identical_duplicates_are_not_conflicts():
    bars, rep = normalize_bars(raw(row(D1, created=1.7e12), row(D1, created=1.8e12)))
    assert len(bars) == 1 and rep.duplicates_removed == 1 and rep.conflicting_duplicate_dates == 0


def test_bad_duplicate_does_not_beat_good_one():
    good = row(D1, created=1.7e12)
    bad_later = row(D1, o=0.0, h=0.0, l=0.0, created=1.9e12)
    bars, rep = normalize_bars(raw(good, bad_later))
    assert bars["close"].tolist() == [105.0] and len(rep.rejected) == 1


def test_null_volume_allowed():
    bars, _ = normalize_bars(raw(row(D1, v=np.nan)))
    assert len(bars) == 1 and np.isnan(bars["volume"].iloc[0])


def test_same_date_different_symbols_are_separate():
    bars, _ = normalize_bars(raw(row(D1, fin_code=1), row(D1, fin_code=2)))
    assert len(bars) == 2


def test_empty_input():
    bars, rep = normalize_bars(raw())
    assert bars.empty and rep.rows_in == 0


def test_missing_dates_inside_symbol_range_only():
    index_bars, _ = normalize_bars(raw(row(D1, fin_code=9), row(D2, fin_code=9), row(D3, fin_code=9)))
    cal = build_calendar(index_bars)
    stock, _ = normalize_bars(raw(row(D1), row(D3)))
    assert find_missing_dates(stock, cal) == {1: [D2]}
    late_start, _ = normalize_bars(raw(row(D3)))
    assert find_missing_dates(late_start, cal) == {}  # before first bar is not a gap


def test_trading_calendar_adds_days_the_index_lacks():
    index_bars, _ = normalize_bars(raw(row(D1, fin_code=9), row(D3, fin_code=9)))
    stocks, _ = normalize_bars(raw(*[row(d, fin_code=c) for c in (1, 2, 3, 4, 5) for d in (D1, D2, D3)],
                                   row(D3 + (D3 - D2), fin_code=1)))  # one stock alone on a later day
    assert build_calendar(index_bars) == [D1, D3]
    assert build_trading_calendar(index_bars, stocks) == [D1, D2, D3]  # D2 added; lone-stock day is not


def test_trading_calendar_ignores_not_yet_listed_symbols():
    index_bars, _ = normalize_bars(raw(row(D1, fin_code=9)))
    stocks, _ = normalize_bars(raw(row(D1, fin_code=1), row(D2, fin_code=1), row(D2, fin_code=2)))
    assert D2 in build_trading_calendar(index_bars, stocks)  # symbol 2 listed on D2 counts, no denominator penalty


def test_fingerprint_deterministic_and_sensitive():
    a, _ = normalize_bars(raw(row(D1), row(D2)))
    b, _ = normalize_bars(raw(row(D2), row(D1)))
    c, _ = normalize_bars(raw(row(D1), row(D2, c=106.0)))
    assert bars_fingerprint(a) == bars_fingerprint(b) != bars_fingerprint(c)
