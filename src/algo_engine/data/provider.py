"""Market data provider interface and shared data types."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

import pandas as pd

RAW_BAR_COLUMNS = [
    "fin_code", "stock_type", "updated_time", "created_at", "trading_date",
    "open_price", "high_price", "low_price", "close_price",
    "volume_traded", "last_day_close_price",
]


@dataclass(frozen=True)
class Instrument:
    fin_code: int
    symbol: str | None
    name: str | None
    isin: str | None
    segment: str | None
    tick_size: float | None
    lot_size: int | None


ACTION_COLUMNS = [
    "fin_code", "corpact_id", "ex_date", "ratio1", "ratio2", "amount", "offer_price",
    "dividend_type", "details",
]


class MarketDataProvider(Protocol):
    def get_instruments(self, fin_codes: Sequence[int]) -> list[Instrument]: ...

    def get_index_members(self, index_fin_code: int) -> list[int]: ...

    def get_corporate_actions(self, fin_codes: Sequence[int]) -> pd.DataFrame: ...

    def get_raw_bars(
        self, fin_codes: Sequence[int], start: date, end: date, stock_type: str = "NSE"
    ) -> pd.DataFrame: ...
