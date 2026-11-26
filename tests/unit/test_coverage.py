"""Coverage diagnostic: missing days are classified, nothing is filled."""
from __future__ import annotations

from datetime import date

import pandas as pd

from algo_engine.data.coverage import coverage_by_year, summarize
from algo_engine.data.normalize import DataQualityReport

CAL = [date(2022, 12, 29), date(2022, 12, 30), date(2023, 1, 2), date(2023, 1, 3), date(2023, 1, 4), date(2023, 1, 5)]


def bars(code, days):
    return pd.DataFrame({"fin_code": code, "trading_date": days, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0})


def test_missing_days_are_classified_by_reason_and_year():
    stock = bars(1, [CAL[0], CAL[1], CAL[2], CAL[5]])                       # 2023-01-03 and 01-04 missing
    index = bars(99, CAL)
    rejected = pd.DataFrame({"fin_code": [1, 1], "trading_date": [CAL[3], CAL[3]],
                             "reject_reason": ["null_price", "null_price"]})  # 01-03 NULL open (two raw rows)
    late = bars(2, [CAL[4], CAL[5]])                                        # first row on 2023-01-04
    report = DataQualityReport(rejected=rejected)
    cov = coverage_by_year(pd.concat([stock, index, late]), report, CAL, index_code=99).set_index(["fin_code", "year"])
    row = cov.loc[(1, 2023)]
    assert (row["expected"], row["valid"], row["rejected_null_price"], row["no_row"]) == (4, 2, 1, 1)
    assert cov.loc[(1, 2022), "coverage"] == 1.0
    assert cov.loc[(2, 2023), "expected"] == 2 and cov.loc[(2, 2023), "coverage"] == 1.0    # starts at its first row
    assert 99 not in cov.index.get_level_values(0)                                         # index excluded
    text = summarize(cov.reset_index())
    assert "2023" in text and "rejected_null_price" in text


def test_other_rejections_and_empty_report():
    stock = bars(1, [CAL[2]])
    rejected = pd.DataFrame({"fin_code": [1], "trading_date": [CAL[3]], "reject_reason": ["high_below_low"]})
    cov = coverage_by_year(stock, DataQualityReport(rejected=rejected), CAL[2:])
    row = cov.iloc[0]
    assert (row["expected"], row["valid"], row["rejected_other"], row["no_row"]) == (4, 1, 1, 2)
    clean = coverage_by_year(stock, DataQualityReport(), CAL[2:3])
    assert clean.iloc[0]["coverage"] == 1.0
