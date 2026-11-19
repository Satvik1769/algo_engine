"""Live read-only checks. Skipped unless PGHOST/PGDATABASE/PGUSER/PGPASSWORD are set."""
import os
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from algo_engine.data.postgres_provider import PostgresMarketDataProvider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("PGPASSWORD"), reason="database credentials not set"),
]

NIFTY_50 = 100256265
RELIANCE = 100325


@pytest.fixture(scope="module")
def provider():
    return PostgresMarketDataProvider.from_env()


def test_nifty50_has_50_unique_members(provider):
    members = provider.get_index_members(NIFTY_50)
    assert len(members) == 50 and RELIANCE in members


def test_instruments_lookup_and_unknown_code(provider):
    (inst,) = provider.get_instruments([RELIANCE])
    assert inst.symbol == "RELIANCE" and inst.isin == "INE002A01018"
    with pytest.raises(LookupError):
        provider.get_instruments([-1])


def test_raw_bars_respect_range_and_columns(provider):
    df = provider.get_raw_bars([RELIANCE], date(2024, 1, 1), date(2024, 1, 31))
    assert not df.empty
    assert df["trading_date"].min() >= date(2024, 1, 1) and df["trading_date"].max() <= date(2024, 1, 31)
    assert set(df["fin_code"]) == {RELIANCE} and set(df["stock_type"]) == {"NSE"}
    assert df["close_price"].dtype.kind == "f"
    assert df["updated_time"].is_monotonic_increasing


def test_unknown_unlisted_range_is_empty(provider):
    assert provider.get_raw_bars([RELIANCE], date(1990, 1, 1), date(1990, 1, 31)).empty


def test_connection_rejects_writes(provider):
    with pytest.raises(DBAPIError):
        with provider._engine.connect() as conn:
            conn.execute(text("CREATE TEMP TABLE _should_fail (x int)"))
