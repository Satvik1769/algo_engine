from datetime import date

import pytest

from algo_engine.data.postgres_provider import PostgresMarketDataProvider, parse_member_codes


def test_parse_handles_spaces_and_empty_items():
    assert parse_member_codes("132541, 132281,100209 , ,132540,") == [132541, 132281, 100209, 132540]


def test_parse_none_is_empty():
    assert parse_member_codes(None) == []


def test_parse_rejects_non_numeric():
    with pytest.raises(ValueError, match="Non-numeric"):
        parse_member_codes("1,abc")


def test_parse_rejects_duplicates():
    with pytest.raises(ValueError, match="Duplicate"):
        parse_member_codes("1,2,1")


def test_raw_bars_validates_arguments_before_touching_db():
    provider = PostgresMarketDataProvider(engine=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="stock_type"):
        provider.get_raw_bars([1], date(2020, 1, 1), date(2020, 2, 1), stock_type="XYZ")
    with pytest.raises(ValueError, match="after end"):
        provider.get_raw_bars([1], date(2020, 2, 1), date(2020, 1, 1))


def test_raw_bars_empty_codes_returns_empty_frame_with_columns():
    provider = PostgresMarketDataProvider(engine=None)  # type: ignore[arg-type]
    df = provider.get_raw_bars([], date(2020, 1, 1), date(2020, 2, 1))
    assert df.empty and "close_price" in df.columns
