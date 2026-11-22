"""Load pipeline: raw bars -> normalize -> adjust -> bar store (Milestone 3c)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd

from algo_engine.data.adjust import AdjustmentConfig, adjust_bars, build_events, find_unexplained_jumps
from algo_engine.data.normalize import (
    DataQualityReport, bars_fingerprint, build_calendar, find_missing_dates, normalize_bars,
)
from algo_engine.data.provider import MarketDataProvider
from algo_engine.data.store import BarStore

NIFTY50_INDEX_FIN_CODE = 100256265


@dataclass
class LoadResult:
    run_id: int
    fingerprint: str
    rows_loaded: int
    report: DataQualityReport
    events: pd.DataFrame           # per-event adjustment report (status applied / already_adjusted / ambiguous...)
    unexplained_jumps: pd.DataFrame


def load_bars(
    provider: MarketDataProvider,
    store: BarStore,
    fin_codes: Sequence[int],
    start: date,
    end: date,
    calendar_code: int = NIFTY50_INDEX_FIN_CODE,
    adjustment: AdjustmentConfig | None = None,
) -> LoadResult:
    """Pull the full requested range, clean and adjust it, and replace the stored bars of these symbols.

    The calendar index (`calendar_code`) is stored too, unadjusted, so the calendar can be rebuilt later.
    """
    codes = sorted({int(c) for c in fin_codes} - {int(calendar_code)})
    if not codes:
        raise ValueError("fin_codes is empty")
    raw = provider.get_raw_bars([*codes, int(calendar_code)], start, end)
    bars, report = normalize_bars(raw)

    is_index = bars["fin_code"] == calendar_code
    calendar = build_calendar(bars[is_index])
    if not calendar:
        raise RuntimeError(f"No bars for calendar index {calendar_code} between {start} and {end}")
    stocks = bars[~is_index]
    report.missing_dates = find_missing_dates(stocks, calendar)

    events = build_events(provider.get_corporate_actions(codes))
    adjusted, event_report = adjust_bars(stocks, events, calendar, adjustment)
    index_bars = bars[is_index].assign(adj_factor=1.0)
    final = pd.concat([adjusted, index_bars], ignore_index=True).sort_values(
        ["fin_code", "trading_date"]).reset_index(drop=True)

    fingerprint = bars_fingerprint(final)
    run_id = store.replace_bars(final, [*codes, int(calendar_code)], event_report, report,
                                start, end, calendar_code, fingerprint)
    return LoadResult(run_id, fingerprint, len(final), report, event_report,
                      find_unexplained_jumps(adjusted, calendar))
