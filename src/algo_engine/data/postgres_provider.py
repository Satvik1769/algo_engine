"""Read-only PostgreSQL market data provider.

Returns RAW rows. Duplicates, zero prices and gaps are left untouched here; detecting and handling
them is the job of the validation/normalization layer (Milestone 3).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import pandas as pd
from sqlalchemy import Engine, bindparam, create_engine, text

from algo_engine.config.settings import DbSettings
from algo_engine.data.provider import ACTION_COLUMNS, RAW_BAR_COLUMNS, Instrument

MARKET_TZ = "Asia/Kolkata"
_ALLOWED_STOCK_TYPES = {"NSE", "BSE"}


def parse_member_codes(companies: str | None) -> list[int]:
    """Parse the comma-separated `companies` text of index_companies_mapping into fin_codes.

    Tolerates spaces and empty items. Raises ValueError on a non-numeric item or duplicate code,
    so corrupt membership data is never silently accepted.
    """
    if companies is None:
        return []
    codes: list[int] = []
    for item in companies.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            codes.append(int(item))
        except ValueError as exc:
            raise ValueError(f"Non-numeric fin_code {item!r} in index membership") from exc
    if len(set(codes)) != len(codes):
        raise ValueError("Duplicate fin_code in index membership")
    return codes


def create_readonly_engine(settings: DbSettings) -> Engine:
    return create_engine(settings.url(), connect_args=settings.connect_args(), pool_pre_ping=True)


class PostgresMarketDataProvider:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @classmethod
    def from_env(cls) -> PostgresMarketDataProvider:
        return cls(create_readonly_engine(DbSettings.from_env()))

    def get_instruments(self, fin_codes: Sequence[int]) -> list[Instrument]:
        if not fin_codes:
            return []
        stmt = text(
            "SELECT fin_code, symbol, comp_name, isin, segment, tick_size, lot_size "
            "FROM company_master WHERE fin_code IN :codes ORDER BY fin_code"
        ).bindparams(bindparam("codes", expanding=True))
        with self._engine.connect() as conn:
            rows = conn.execute(stmt, {"codes": [int(c) for c in fin_codes]}).all()
        found = {r.fin_code for r in rows}
        missing = sorted(set(int(c) for c in fin_codes) - found)
        if missing:
            raise LookupError(f"fin_codes not found in company_master: {missing}")
        return [
            Instrument(r.fin_code, r.symbol, r.comp_name, r.isin, r.segment, r.tick_size, r.lot_size)
            for r in rows
        ]

    def get_index_members(self, index_fin_code: int) -> list[int]:
        """Current constituents (snapshot, NOT point-in-time) of an index."""
        stmt = text("SELECT companies FROM index_companies_mapping WHERE fin_code = :fc")
        with self._engine.connect() as conn:
            row = conn.execute(stmt, {"fc": int(index_fin_code)}).first()
        if row is None:
            raise LookupError(f"Index fin_code {index_fin_code} not in index_companies_mapping")
        return parse_member_codes(row.companies)

    def get_corporate_actions(self, fin_codes: Sequence[int]) -> pd.DataFrame:
        """Raw corporate-action rows that have an ex-date (announcement-only rows are excluded).

        `corpact_id` encodes the kind (see docs/PROJECT_PLAN.md K15). Rows are returned as stored,
        including the duplicates across ids; deduplication happens in the adjustment layer.
        """
        if not fin_codes:
            return pd.DataFrame(columns=ACTION_COLUMNS)
        stmt = text(
            "SELECT fin_code::bigint AS fin_code, corpact_id::int AS corpact_id, "
            "left(ex_date, 10)::date AS ex_date, ratio1::float8 AS ratio1, ratio2::float8 AS ratio2, "
            "amount::float8 AS amount, offer_price::float8 AS offer_price, dividend_type, details "
            "FROM corporate_actions "
            "WHERE fin_code IN :codes AND ex_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' "
            "ORDER BY fin_code, ex_date, corpact_id"
        ).bindparams(bindparam("codes", expanding=True))
        with self._engine.connect() as conn:
            df = pd.read_sql_query(stmt, conn, params={"codes": [int(c) for c in fin_codes]})
        return df[ACTION_COLUMNS]

    def get_raw_bars(
        self, fin_codes: Sequence[int], start: date, end: date, stock_type: str = "NSE"
    ) -> pd.DataFrame:
        """Raw rows with IST `trading_date` between start and end inclusive.

        Ordered by (fin_code, updated_time, created_at). Not deduplicated or cleaned.
        """
        if stock_type not in _ALLOWED_STOCK_TYPES:
            raise ValueError(f"stock_type must be one of {sorted(_ALLOWED_STOCK_TYPES)}")
        if start > end:
            raise ValueError(f"start {start} is after end {end}")
        if not fin_codes:
            return pd.DataFrame(columns=RAW_BAR_COLUMNS)
        stmt = text(
            "SELECT fin_code, stock_type, updated_time, created_at::float8 AS created_at, "
            f"(to_timestamp(updated_time / 1000.0) AT TIME ZONE '{MARKET_TZ}')::date AS trading_date, "
            "open_price::float8 AS open_price, high_price::float8 AS high_price, "
            "low_price::float8 AS low_price, close_price::float8 AS close_price, "
            "volume_traded::float8 AS volume_traded, "
            "last_day_close_price::float8 AS last_day_close_price "
            "FROM stock_prices_five_years "
            "WHERE fin_code IN :codes AND stock_type = :stock_type "
            f"AND (to_timestamp(updated_time / 1000.0) AT TIME ZONE '{MARKET_TZ}')::date "
            "BETWEEN :start AND :end "
            "ORDER BY fin_code, updated_time, created_at"
        ).bindparams(bindparam("codes", expanding=True))
        params = {
            "codes": [int(c) for c in fin_codes],
            "stock_type": stock_type,
            "start": start,
            "end": end,
        }
        with self._engine.connect() as conn:
            df = pd.read_sql_query(stmt, conn, params=params)
        return df[RAW_BAR_COLUMNS]
