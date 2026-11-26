from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from algo_engine.data.adjust import (
    STATUS_ALREADY_ADJUSTED, STATUS_AMBIGUOUS_GAP, STATUS_AMBIGUOUS_SMALL, STATUS_APPLIED,
    STATUS_INVALID, STATUS_OUT_OF_RANGE, adjust_bars, build_events,
)

CAL = [date(2024, 1, 1) + timedelta(days=i) for i in range(60)]  # synthetic calendar, every day trades


def make_bars(closes, drop=(), fin_code=1):
    rows = []
    for i, c in enumerate(closes):
        if i in drop:
            continue
        rows.append(dict(fin_code=fin_code, trading_date=CAL[i], open=c, high=c * 1.01, low=c * 0.99,
                         close=c, volume=1000.0, prev_close=c))
    return pd.DataFrame(rows)


def action(corpact_id, ex_idx, r1, r2, fin_code=1):
    return dict(fin_code=fin_code, corpact_id=corpact_id, ex_date=CAL[ex_idx], ratio1=r1, ratio2=r2,
                amount=np.nan, offer_price=np.nan, dividend_type=None, details="")


def events(*acts):
    return build_events(pd.DataFrame(list(acts)))


def test_factor_formulas():
    ev = events(action(76, 5, 1, 1), action(76, 10, 2, 1), action(76, 15, 1, 3),
                action(90, 20, 1, 10), action(90, 25, 2, 10))
    assert ev["factor"].round(6).tolist() == [0.5, round(1 / 3, 6), 0.75, 0.1, 0.2]


def test_duplicates_across_ids_collapse_and_same_day_events_combine():
    ev = events(action(76, 5, 1, 1), action(68, 5, 1, 1))
    assert len(ev) == 1 and ev["factor"].iloc[0] == 0.5
    combined = events(action(76, 5, 1, 1), action(90, 5, 1, 5))
    assert len(combined) == 1 and combined["factor"].iloc[0] == pytest.approx(0.1)


def test_invalid_ratio_is_reported_not_dropped():
    ev = events(action(76, 5, np.nan, 1))
    assert ev["status"].tolist() == [STATUS_INVALID]


def test_unadjusted_event_is_applied_to_earlier_bars_only():
    closes = [200.0] * 10 + [100.0] * 10   # 1:1 bonus on bar 10, price halves
    bars = make_bars(closes)
    adj, rep = adjust_bars(bars, events(action(76, 10, 1, 1)), CAL)
    assert rep["status"].tolist() == [STATUS_APPLIED]
    assert adj["close"].tolist() == [100.0] * 20
    assert adj.loc[:9, "volume"].eq(2000.0).all() and adj.loc[10:, "volume"].eq(1000.0).all()
    assert adj.loc[:9, "adj_factor"].eq(0.5).all() and adj.loc[10:, "adj_factor"].eq(1.0).all()
    assert bars["close"].iloc[0] == 200.0  # input untouched


def test_already_adjusted_event_is_left_alone():
    bars = make_bars([100.0] * 20)
    adj, rep = adjust_bars(bars, events(action(76, 10, 1, 1)), CAL)
    assert rep["status"].tolist() == [STATUS_ALREADY_ADJUSTED]
    assert adj["close"].tolist() == bars["close"].tolist()


def test_wrong_listed_ex_date_is_corrected_from_the_price_jump():
    closes = [500.0] * 15 + [100.0] * 15      # 1:5 split, real jump on bar 15, listed on bar 10
    adj, rep = adjust_bars(make_bars(closes), events(action(90, 10, 1, 5)), CAL)
    assert rep["status"].tolist() == [STATUS_APPLIED]
    assert rep["apply_date"].iloc[0] == CAL[15] and "jump seen" in rep["note"].iloc[0]
    assert adj["close"].tolist() == pytest.approx([100.0] * 30)


def test_missing_days_around_ex_date_make_the_event_ambiguous_not_guessed():
    closes = [500.0] * 10 + [100.0] * 10
    bars = make_bars(closes, drop=range(6, 15))    # nine days around the ex-date are missing
    adj, rep = adjust_bars(bars, events(action(90, 10, 1, 5)), CAL)
    assert rep["status"].tolist() == [STATUS_AMBIGUOUS_GAP]
    assert adj["close"].tolist() == bars["close"].tolist()


def test_factor_close_to_one_is_not_decided():
    _, rep = adjust_bars(make_bars([100.0] * 20), events(action(76, 10, 1, 9)), CAL)  # factor 0.9
    assert rep["status"].tolist() == [STATUS_AMBIGUOUS_SMALL]


def test_event_outside_data_range():
    _, rep = adjust_bars(make_bars([100.0] * 20), events(action(76, 40, 1, 1)), CAL)
    assert rep["status"].tolist() == [STATUS_OUT_OF_RANGE]


def test_two_unadjusted_events_compound():
    closes = [400.0] * 10 + [200.0] * 10 + [100.0] * 10   # two 1:1 bonuses
    adj, rep = adjust_bars(make_bars(closes), events(action(76, 10, 1, 1), action(76, 20, 1, 1)), CAL)
    assert rep["status"].tolist() == [STATUS_APPLIED, STATUS_APPLIED]
    assert adj["close"].tolist() == pytest.approx([100.0] * 30)


def test_symbols_are_adjusted_independently():
    a = make_bars([200.0] * 10 + [100.0] * 10, fin_code=1)
    b = make_bars([100.0] * 20, fin_code=2)
    adj, rep = adjust_bars(pd.concat([a, b]), events(action(76, 10, 1, 1, fin_code=1)), CAL)
    assert adj[adj.fin_code == 2]["close"].eq(100.0).all()
    assert adj[adj.fin_code == 1]["close"].eq(100.0).all()


def test_short_gap_across_the_ex_date_still_matches_the_jump():
    closes = [200.0] * 10 + [100.0] * 10
    bars = make_bars(closes, drop=(10, 11))          # ex-date bars missing, jump spans 2 missing days
    adj, rep = adjust_bars(bars, events(action(76, 10, 1, 1)), CAL)
    assert rep["status"].tolist() == [STATUS_APPLIED] and rep["apply_date"].iloc[0] == CAL[12]
    assert adj["close"].tolist() == [100.0] * 18


def test_gap_in_window_never_counts_as_already_adjusted():
    # Real jump hidden inside a long gap; the pair around the listed ex-date looks flat.
    closes = [500.0] * 13 + [100.0] * 12
    bars = make_bars(closes, drop=range(13, 20))     # 7 missing days right after the last pre-jump bar
    _, rep = adjust_bars(bars, events(action(90, 10, 1, 5)), CAL)
    assert rep["status"].tolist() == [STATUS_AMBIGUOUS_GAP]


def test_unexplained_jumps_flag_unhandled_events_and_ignore_gaps():
    from algo_engine.data.adjust import find_unexplained_jumps
    bars = make_bars([100.0] * 5 + [50.0] * 5 + [50.0] * 5, drop=(11,))
    jumps = find_unexplained_jumps(bars, CAL)
    assert jumps["trading_date"].tolist() == [CAL[5]] and jumps["ratio"].iloc[0] == pytest.approx(0.5)
    # a move that spans a missing day is not judged
    gap_bars = make_bars([100.0] * 5 + [50.0] * 5, drop=(5,))
    assert find_unexplained_jumps(gap_bars, CAL).empty
