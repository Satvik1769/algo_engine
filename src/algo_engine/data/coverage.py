"""Per-symbol, per-year data coverage against the index trading calendar (read-only diagnostic).

For each stock the expected days are the calendar days from the symbol's first row (valid or not) to the last
calendar day. A day is `valid` if a normalized bar exists, otherwise it is missing for one reason:
  rejected_null_price  a source row exists but an OHLC value is NULL (K19: mostly the open)
  rejected_other       a source row exists but fails another OHLC check
  no_row               the source has no row for that day (K18)
Nothing is repaired or filled; this only measures what the backtest cannot see.

    python -m algo_engine.data.coverage [--start 2018-01-01] [--end 2026-12-31] [--csv out.csv]
"""
from __future__ import annotations

import argparse
from datetime import date

import pandas as pd

from algo_engine.data.normalize import DataQualityReport, build_calendar, build_trading_calendar

COLUMNS = ["fin_code", "year", "expected", "valid", "rejected_null_price", "rejected_other", "no_row", "coverage"]


def coverage_by_year(bars: pd.DataFrame, report: DataQualityReport, calendar: list[date],
                     index_code: int | None = None) -> pd.DataFrame:
    """One row per (fin_code, year). `bars` and `report` come from `normalize_bars`; `calendar` from the index."""
    cal = pd.Series(sorted(calendar))
    stocks = bars if index_code is None else bars[bars["fin_code"] != index_code]
    valid_days = {c: set(g["trading_date"]) for c, g in stocks.groupby("fin_code")}
    rej = report.rejected
    if index_code is not None and not rej.empty:
        rej = rej[rej["fin_code"] != index_code]
    null_days: dict[int, set[date]] = {}
    other_days: dict[int, set[date]] = {}
    first_row: dict[int, date] = {}
    for c, g in stocks.groupby("fin_code"):
        first_row[c] = g["trading_date"].min()
    if not rej.empty:
        rej = rej[rej["trading_date"].notna()]
        for c, g in rej.groupby("fin_code"):
            first_row[c] = min(first_row.get(c, g["trading_date"].min()), g["trading_date"].min())
            null_days[c] = set(g.loc[g["reject_reason"] == "null_price", "trading_date"])
            other_days[c] = set(g.loc[g["reject_reason"] != "null_price", "trading_date"])
    rows = []
    for c in sorted(first_row):
        window = cal[cal >= first_row[c]]
        for year, days in window.groupby(pd.to_datetime(window).dt.year):
            valid = sum(d in valid_days.get(c, ()) for d in days)
            missing = [d for d in days if d not in valid_days.get(c, ())]
            null = sum(d in null_days.get(c, ()) for d in missing)
            other = sum(d in other_days.get(c, ()) and d not in null_days.get(c, ()) for d in missing)
            rows.append((c, int(year), len(days), valid, null, other, len(missing) - null - other,
                         valid / len(days)))
    return pd.DataFrame(rows, columns=COLUMNS)


def summarize(cov: pd.DataFrame) -> str:
    by_year = cov.groupby("year")[["expected", "valid", "rejected_null_price", "rejected_other", "no_row"]].sum()
    by_year["coverage"] = by_year["valid"] / by_year["expected"]
    by_year["symbols_below_95pct"] = cov[cov["coverage"] < 0.95].groupby("year").size().reindex(by_year.index).fillna(0).astype(int)
    return by_year.to_string(float_format=lambda x: f"{x:.3f}")


def main(argv: list[str] | None = None) -> int:
    from algo_engine.config.settings import DbSettings
    from algo_engine.data.normalize import normalize_bars
    from algo_engine.data.pipeline import NIFTY50_INDEX_FIN_CODE
    from algo_engine.data.postgres_provider import PostgresMarketDataProvider, create_readonly_engine

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=date.fromisoformat, default=date(2018, 1, 1))
    ap.add_argument("--end", type=date.fromisoformat, default=date(2026, 12, 31))
    ap.add_argument("--index", type=int, default=NIFTY50_INDEX_FIN_CODE)
    ap.add_argument("--csv", help="write the per-symbol, per-year table here")
    args = ap.parse_args(argv)

    provider = PostgresMarketDataProvider(create_readonly_engine(DbSettings.from_env()))
    codes = provider.get_index_members(args.index)
    bars, report = normalize_bars(provider.get_raw_bars([*codes, args.index], args.start, args.end))
    is_index = bars["fin_code"] == args.index
    index_days = set(build_calendar(bars[is_index]))
    calendar = build_trading_calendar(bars[is_index], bars[~is_index])
    cov = coverage_by_year(bars, report, calendar, args.index)
    print(f"{len(codes)} symbols, {len(calendar)} calendar days {calendar[0]} -> {calendar[-1]} "
          f"({len(set(calendar) - index_days)} added from stock bars, index lacks them)")
    print(summarize(cov))
    worst = cov.groupby("fin_code")[["expected", "valid"]].sum()
    worst["coverage"] = worst["valid"] / worst["expected"]
    print("\nLowest overall coverage:")
    print(worst.sort_values("coverage").head(10).to_string(float_format=lambda x: f"{x:.3f}"))
    if args.csv:
        cov.to_csv(args.csv, index=False)
        print(f"written to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
