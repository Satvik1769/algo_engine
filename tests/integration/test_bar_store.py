"""Round trip through the local bar store using a throwaway schema. Skipped if local Postgres is down."""
from datetime import date

import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from algo_engine.config.settings import StoreSettings
from algo_engine.data.normalize import DataQualityReport, bars_fingerprint
from algo_engine.data.pipeline import load_bars
from algo_engine.data.store import BarStore, create_store_engine

pytestmark = pytest.mark.integration
SCHEMA = "algo_test"


@pytest.fixture()
def store():
    settings = StoreSettings(schema=SCHEMA)
    engine = create_store_engine(settings)
    try:
        engine.connect().close()
    except DBAPIError:
        pytest.skip("local PostgreSQL not reachable")
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    s = BarStore(engine, SCHEMA)
    s.migrate()
    yield s
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))


def _bars(fin_code=1, days=(date(2024, 1, 1), date(2024, 1, 2))):
    return pd.DataFrame({
        "fin_code": fin_code, "trading_date": list(days),
        "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0,
        "volume": [100.0, None][: len(days)], "prev_close": 10.0, "adj_factor": 1.0,
    })


def _replace(store, bars, codes=(1,)):
    return store.replace_bars(bars, codes, pd.DataFrame(), DataQualityReport(rows_in=len(bars), rows_out=len(bars)),
                              date(2024, 1, 1), date(2024, 1, 2), 99, bars_fingerprint(bars))


def test_migrate_is_idempotent(store):
    store.migrate()


def test_round_trip_keeps_nulls_and_order(store):
    _replace(store, _bars())
    out = store.read_bars([1])
    assert list(out["trading_date"]) == [date(2024, 1, 1), date(2024, 1, 2)]
    assert pd.isna(out["volume"].iloc[1]) and out["volume"].iloc[0] == 100.0
    assert store.latest_run()["rows_loaded"] == 2


def test_replace_swaps_only_the_given_symbols(store):
    _replace(store, pd.concat([_bars(1), _bars(2)]), codes=(1, 2))
    _replace(store, _bars(1, days=(date(2024, 1, 3),)), codes=(1,))
    assert list(store.read_bars([1])["trading_date"]) == [date(2024, 1, 3)]
    assert len(store.read_bars([2])) == 2


def test_failed_load_rolls_back_and_keeps_old_data(store):
    _replace(store, _bars())
    bad = _bars(days=(date(2024, 1, 5), date(2024, 1, 5)))  # duplicate primary key
    with pytest.raises(IntegrityError):
        _replace(store, bad)
    assert len(store.read_bars([1])) == 2


def test_invalid_ohlc_is_rejected_by_the_database(store):
    bad = _bars()
    bad.loc[0, "high"] = 5.0
    with pytest.raises(IntegrityError):
        _replace(store, bad)


class _FakeProvider:
    def get_raw_bars(self, fin_codes, start, end, stock_type="NSE"):
        rows = []
        for code in fin_codes:
            for i, d in enumerate([date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]):
                rows.append(dict(fin_code=code, stock_type="NSE", updated_time=i, created_at=1.7e12,
                                 trading_date=d, open_price=10.0, high_price=12.0, low_price=9.0,
                                 close_price=11.0, volume_traded=5.0, last_day_close_price=10.0))
        return pd.DataFrame(rows)

    def get_corporate_actions(self, fin_codes):
        return pd.DataFrame(columns=["fin_code", "corpact_id", "ex_date", "ratio1", "ratio2",
                                     "amount", "offer_price", "dividend_type", "details"])


def test_pipeline_loads_stock_and_calendar_index(store):
    result = load_bars(_FakeProvider(), store, [1], date(2024, 1, 1), date(2024, 1, 3), calendar_code=500)
    assert result.rows_loaded == 6
    assert len(store.read_bars([1])) == 3 and len(store.read_bars([500])) == 3
    assert store.latest_run()["fingerprint"] == result.fingerprint


def test_dividends_round_trip_and_replace(store):
    divs = pd.DataFrame({"fin_code": [1, 1], "ex_date": [date(2024, 1, 2), date(2024, 1, 2)],
                         "amount": [4.0, 1.0], "adj_factor": [0.5, 0.5], "adj_amount": [2.0, 0.5],
                         "dividend_type": ["Final", None]})
    bars = _bars()
    store.replace_bars(bars, (1,), pd.DataFrame(), DataQualityReport(rows_in=2, rows_out=2),
                       date(2024, 1, 1), date(2024, 1, 2), 99, bars_fingerprint(bars), divs)
    out = store.read_dividends([1], date(2024, 1, 1), date(2024, 1, 31))
    assert list(out["amount"]) == [0.5, 2.0] and list(out["ex_date"]) == [date(2024, 1, 2)] * 2
    assert store.read_dividends([1], date(2024, 2, 1), None).empty
    _replace(store, bars)                                    # dividends=None leaves them alone
    assert len(store.read_dividends([1])) == 2
    store.replace_dividends([1], divs.iloc[:1])
    assert list(store.read_dividends([1])["amount"]) == [2.0]
    with pytest.raises(IntegrityError):
        store.replace_dividends([1], pd.concat([divs.iloc[:1], divs.iloc[:1]]))   # duplicate key
    assert len(store.read_dividends([1])) == 1               # rolled back
