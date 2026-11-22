"""Split/bonus price adjustment, reconciled against the price series (ADR-0010, Milestone 3b).

Why reconcile: the stored prices are only partly adjusted by the vendor (docs/PROJECT_PLAN.md K16),
and some ex-dates are wrong (K17). Applying every event would double-adjust, applying none would
leave false crashes. For each event we look at the price jump `open[t] / close[t-1]` around the
ex-date and compare it with the expected factor `f`:

* a jump close to `f` (within `tolerance`) means the price is NOT adjusted: apply `f` to all bars
  before that day (prices * f, volume / f);
* no such jump, no missing days in the search window, and the ex-date jump close to 1 means the vendor
  already adjusted it: do nothing. Missing days could hide a real jump, so a gap in the window never
  counts as evidence of "already adjusted";
* anything else (long gaps, factor too close to 1 to tell, unexplained jump) is reported as ambiguous
  and NOT applied. Ambiguous events must be resolved by a human or by loading the missing days.
A jump that spans a short gap (<= max_gap_days missing days) can still be matched: the bars before the
first post-jump bar are all pre-ex-date, so adjusting them is correct whatever the true ex-date was.

Factors (verified against price jumps, K15): bonus ratio1:ratio2 = bonus shares:held shares,
factor = ratio2 / (ratio1 + ratio2); split ratio1:ratio2 = new face value:old face value,
factor = ratio1 / ratio2. Rights issues and dividends are not price-adjusted here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

BONUS_IDS = {68, 76}
SPLIT_IDS = {90, 91}
PRICE_COLS = ["open", "high", "low", "close", "prev_close"]

STATUS_APPLIED = "applied"
STATUS_ALREADY_ADJUSTED = "already_adjusted"
STATUS_AMBIGUOUS_GAP = "ambiguous_gap"
STATUS_AMBIGUOUS = "ambiguous_unexplained"
STATUS_AMBIGUOUS_SMALL = "ambiguous_small_factor"
STATUS_OUT_OF_RANGE = "out_of_range"
STATUS_INVALID = "invalid_ratio"


@dataclass(frozen=True)
class AdjustmentConfig:
    tolerance: float = 0.10  # allowed relative error between observed jump and expected factor
    window_bars: int = 30    # how many bars either side of the listed ex-date to search for the jump
    max_gap_days: int = 5    # a jump spanning at most this many missing trading days may still be matched


def build_events(actions: pd.DataFrame) -> pd.DataFrame:
    """Split/bonus events with a price factor, deduplicated across corpact_ids.

    Events of one stock on the same ex-date are combined (factors multiplied). Rows with missing or
    non-positive ratios are returned with status `invalid_ratio` so they are visible, not dropped.
    """
    cols = ["fin_code", "ex_date", "kind", "factor", "status"]
    rows = actions[actions["corpact_id"].isin(BONUS_IDS | SPLIT_IDS)].copy()
    if rows.empty:
        return pd.DataFrame(columns=cols)
    rows["kind"] = np.where(rows["corpact_id"].isin(BONUS_IDS), "bonus", "split")
    rows = rows.drop_duplicates(subset=["fin_code", "ex_date", "kind", "ratio1", "ratio2"])
    valid = (rows["ratio1"] > 0) & (rows["ratio2"] > 0)
    rows["factor"] = np.where(
        rows["kind"] == "bonus",
        rows["ratio2"] / (rows["ratio1"] + rows["ratio2"]),
        rows["ratio1"] / rows["ratio2"],
    )
    rows.loc[~valid, "factor"] = np.nan
    rows["status"] = np.where(valid, "", STATUS_INVALID)
    good = rows[valid].groupby(["fin_code", "ex_date"], as_index=False).agg(
        kind=("kind", "+".join), factor=("factor", "prod"))
    good["status"] = ""
    bad = rows[~valid][cols]
    return pd.concat([good[cols], bad], ignore_index=True).sort_values(["fin_code", "ex_date"]).reset_index(drop=True)


def _classify(bars: pd.DataFrame, pos: dict[date, int], ex_date: date, factor: float,
              cfg: AdjustmentConfig, claimed: set[date]) -> tuple[str, date | None, float | None, str]:
    """Return (status, apply_date, observed_ratio, note) for one event on one symbol's bars."""
    dates = bars["trading_date"].tolist()
    n = len(dates)
    k = next((i for i, d in enumerate(dates) if d >= ex_date), None)
    if k is None or k == 0:
        return STATUS_OUT_OF_RANGE, None, None, "no bars before or after the ex-date"
    if abs(1.0 - factor) <= 2 * cfg.tolerance:
        return STATUS_AMBIGUOUS_SMALL, None, None, "factor too close to 1 to separate from market moves"
    opens, closes = bars["open"].to_numpy(), bars["close"].to_numpy()
    p = np.array([pos.get(d, -10**9) for d in dates])
    ratio = np.full(n, np.nan)
    gap_days = np.full(n, 10**6)               # missing calendar days before each bar (huge if unknown)
    ratio[1:] = opens[1:] / closes[:-1]
    gap_days[1:] = p[1:] - p[:-1] - 1

    lo, hi = max(1, k - cfg.window_bars), min(n - 1, k + cfg.window_bars)
    idx = [i for i in range(lo, hi + 1)
           if 0 <= gap_days[i] <= cfg.max_gap_days and dates[i] not in claimed and abs(ratio[i] / factor - 1.0) <= cfg.tolerance]
    if idx:
        # nearest to the listed ex-date first, then best factor match; a jump serves only one event
        i = min(idx, key=lambda j: (abs(j - k), abs(ratio[j] / factor - 1.0)))
        claimed.add(dates[i])
        note = "" if dates[i] == dates[k] else f"ex-date listed {ex_date}, jump seen on {dates[i]}"
        return STATUS_APPLIED, dates[i], float(ratio[i]), note
    if (gap_days[lo:hi + 1] != 0).any():
        i = lo + int(np.argmax(gap_days[lo:hi + 1]))
        return (STATUS_AMBIGUOUS_GAP, None, None,
                f"missing trading days in the search window (e.g. between {dates[i-1]} and {dates[i]})")
    if abs(ratio[k] - 1.0) <= cfg.tolerance:
        return STATUS_ALREADY_ADJUSTED, None, float(ratio[k]), ""
    return STATUS_AMBIGUOUS, None, float(ratio[k]), "jump matches neither 1 nor the expected factor"


def adjust_bars(bars: pd.DataFrame, events: pd.DataFrame, calendar: list[date],
                cfg: AdjustmentConfig | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (adjusted bars with `adj_factor`, per-event report). Input bars are not modified."""
    cfg = cfg or AdjustmentConfig()
    pos = {d: i for i, d in enumerate(calendar)}
    out = bars.sort_values(["fin_code", "trading_date"]).reset_index(drop=True).copy()
    out["adj_factor"] = 1.0
    report_rows: list[dict[str, object]] = []

    for fin_code, ev in events.groupby("fin_code"):
        mask = out["fin_code"] == fin_code
        original = out.loc[mask].copy()  # reconcile against the unmodified series for every event
        claimed: set[date] = set()
        for e in ev.itertuples(index=False):
            if e.status == STATUS_INVALID:
                report_rows.append(dict(fin_code=fin_code, ex_date=e.ex_date, kind=e.kind, factor=np.nan,
                                        status=STATUS_INVALID, apply_date=None, observed_ratio=None, note=""))
                continue
            status, apply_date, observed, note = _classify(original, pos, e.ex_date, float(e.factor), cfg, claimed)
            if status == STATUS_APPLIED:
                before = mask & (out["trading_date"] < apply_date)
                out.loc[before, PRICE_COLS] = out.loc[before, PRICE_COLS] * e.factor
                out.loc[before, "volume"] = out.loc[before, "volume"] / e.factor
                out.loc[before, "adj_factor"] *= e.factor
            report_rows.append(dict(fin_code=fin_code, ex_date=e.ex_date, kind=e.kind, factor=float(e.factor),
                                    status=status, apply_date=apply_date, observed_ratio=observed, note=note))
    report = pd.DataFrame(report_rows, columns=[
        "fin_code", "ex_date", "kind", "factor", "status", "apply_date", "observed_ratio", "note"])
    return out, report


def find_unexplained_jumps(bars: pd.DataFrame, calendar: list[date], low: float = 0.8,
                           high: float = 1.25) -> pd.DataFrame:
    """Overnight jumps `open[t] / close[t-1]` outside [low, high] between calendar-consecutive bars.

    Run this on the ADJUSTED bars. Anything left is either a genuine gap/crash or a corporate action
    missing from `corporate_actions` (K20). Nothing is changed; the rows are for review.
    """
    pos = {d: i for i, d in enumerate(calendar)}
    rows: list[dict[str, object]] = []
    for fin_code, g in bars.sort_values("trading_date").groupby("fin_code"):
        d = g["trading_date"].tolist()
        o, c = g["open"].to_numpy(), g["close"].to_numpy()
        for i in range(1, len(d)):
            if pos.get(d[i], -9) - pos.get(d[i - 1], -99) != 1:
                continue
            r = o[i] / c[i - 1]
            if r < low or r > high:
                rows.append(dict(fin_code=fin_code, trading_date=d[i], ratio=float(r)))
    return pd.DataFrame(rows, columns=["fin_code", "trading_date", "ratio"])
