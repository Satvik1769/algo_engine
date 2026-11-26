"""Validation and normalization of raw bars (Milestone 3a).

Raw rows from the provider are checked, invalid rows are rejected WITH a reason (never repaired or
dropped silently), duplicates per (fin_code, trading_date) are resolved by a fixed rule, and the
result is a canonical, unique, sorted bar table plus a DataQualityReport.

Rules, in order:
1. Reject rows with null/non-positive open/high/low/close, or inconsistent OHLC
   (high < low, high < max(open, close), low > min(open, close)). Null volume is allowed.
2. Among remaining rows for the same (fin_code, trading_date), keep the one with the latest
   created_at (normalized to epoch ms; the raw column mixes seconds and milliseconds).
   If the duplicates disagree on OHLC, the date is counted as a conflicting duplicate.
Gaps against a trading calendar are reported by `find_missing_dates`, never filled.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

BAR_COLUMNS = [
    "fin_code", "trading_date", "open", "high", "low", "close", "volume", "prev_close",
]
_PRICE_COLS = ["open_price", "high_price", "low_price", "close_price"]
_SECONDS_THRESHOLD = 1e11  # epoch values below this are seconds (1e11 s is year 5138; 1e11 ms is 1973)


@dataclass
class DataQualityReport:
    rows_in: int = 0
    rows_out: int = 0
    rejected: pd.DataFrame = field(default_factory=pd.DataFrame)  # raw rows + `reject_reason`
    duplicates_removed: int = 0
    conflicting_duplicate_dates: int = 0
    missing_dates: dict[int, list[date]] = field(default_factory=dict)

    def reject_counts(self) -> dict[str, int]:
        if self.rejected.empty:
            return {}
        return self.rejected["reject_reason"].value_counts().to_dict()

    def summary(self) -> str:
        missing = sum(len(v) for v in self.missing_dates.values())
        return (
            f"rows_in={self.rows_in} rows_out={self.rows_out} rejected={len(self.rejected)} "
            f"{self.reject_counts()} duplicates_removed={self.duplicates_removed} "
            f"conflicting_duplicate_dates={self.conflicting_duplicate_dates} missing_dates={missing}"
        )


def _created_at_ms(created_at: pd.Series) -> pd.Series:
    values = created_at.fillna(0.0).astype(float)
    return values.where(values >= _SECONDS_THRESHOLD, values * 1000.0)


def _reject_reasons(raw: pd.DataFrame) -> pd.Series:
    """First applicable reason per row, empty string when the row is valid."""
    o, h, l, c = (raw[col] for col in _PRICE_COLS)
    reasons = pd.Series("", index=raw.index, dtype=object)
    checks = [
        ("null_price", raw[_PRICE_COLS].isna().any(axis=1)),
        ("non_positive_price", (raw[_PRICE_COLS] <= 0).any(axis=1)),
        ("high_below_low", h < l),
        ("high_below_open_or_close", h < pd.concat([o, c], axis=1).max(axis=1)),
        ("low_above_open_or_close", l > pd.concat([o, c], axis=1).min(axis=1)),
        ("null_trading_date", raw["trading_date"].isna()),
    ]
    for name, mask in reversed(checks):  # reversed so the first check in the list wins
        reasons = reasons.mask(mask.fillna(False), name)
    return reasons


def normalize_bars(raw: pd.DataFrame) -> tuple[pd.DataFrame, DataQualityReport]:
    report = DataQualityReport(rows_in=len(raw))
    if raw.empty:
        return pd.DataFrame(columns=BAR_COLUMNS), report

    df = raw.copy()
    df["reject_reason"] = _reject_reasons(df)
    report.rejected = df[df["reject_reason"] != ""].reset_index(drop=True)
    valid = df[df["reject_reason"] == ""].drop(columns=["reject_reason"]).copy()

    valid["_created_ms"] = _created_at_ms(valid["created_at"])
    key = ["fin_code", "trading_date"]
    distinct_ohlc_per_date = valid.drop_duplicates(subset=key + _PRICE_COLS).groupby(key).size()
    report.conflicting_duplicate_dates = int((distinct_ohlc_per_date > 1).sum())

    valid = valid.sort_values(key + ["_created_ms", "updated_time"], kind="mergesort")
    deduped = valid.drop_duplicates(subset=key, keep="last")
    report.duplicates_removed = len(valid) - len(deduped)

    bars = deduped.rename(columns={
        "open_price": "open", "high_price": "high", "low_price": "low", "close_price": "close",
        "volume_traded": "volume", "last_day_close_price": "prev_close",
    })[BAR_COLUMNS].sort_values(key).reset_index(drop=True)
    report.rows_out = len(bars)
    return bars, report


def build_calendar(index_bars: pd.DataFrame) -> list[date]:
    """Trading calendar = the dates on which the benchmark index traded (sorted, unique)."""
    return sorted(set(index_bars["trading_date"]))


def build_trading_calendar(index_bars: pd.DataFrame, stock_bars: pd.DataFrame, min_share: float = 0.8) -> list[date]:
    """Index trading dates plus any date on which at least `min_share` of the already-listed stocks have a bar.

    The index series can have gaps that the stocks do not (K26: 133 weekdays in 2025-2026), and a calendar with
    holes makes the adjuster and the backtest skip real sessions. A symbol counts as listed from its first bar.
    """
    index_days = set(index_bars["trading_date"])
    if stock_bars.empty:
        return sorted(index_days)
    first = stock_bars.groupby("fin_code")["trading_date"].min().sort_values()
    first_dates = first.to_numpy()
    have = stock_bars.groupby("trading_date")["fin_code"].nunique()
    listed = pd.Series(first_dates).searchsorted(have.index.to_numpy(), side="right")
    stock_days = set(have.index[have.to_numpy() >= min_share * listed.clip(min=1)])
    return sorted(index_days | stock_days)


def find_missing_dates(bars: pd.DataFrame, calendar: list[date]) -> dict[int, list[date]]:
    """Calendar dates with no bar between each symbol's first and last bar. Gaps are reported, not filled."""
    missing: dict[int, list[date]] = {}
    cal = pd.Series(calendar)
    for fin_code, group in bars.groupby("fin_code"):
        have = set(group["trading_date"])
        lo, hi = min(have), max(have)
        gaps = [d for d in cal[(cal >= lo) & (cal <= hi)] if d not in have]
        if gaps:
            missing[int(fin_code)] = gaps
    return missing


def bars_fingerprint(bars: pd.DataFrame) -> str:
    """Deterministic SHA-256 of the canonical bars; stored with each run for reproducibility."""
    ordered = bars[BAR_COLUMNS].sort_values(["fin_code", "trading_date"]).reset_index(drop=True)
    row_hashes = pd.util.hash_pandas_object(ordered, index=False).to_numpy().tobytes()
    return hashlib.sha256(row_hashes).hexdigest()
