"""Historical data loading and preprocessing."""

from pathlib import Path
from typing import Iterable

import pandas as pd


REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


def clean_historical_data(
    data: pd.DataFrame,
    *,
    symbol: str | None = None,
    corporate_action_adjusted: bool = True,
) -> pd.DataFrame:
    """Return a chronologically sorted OHLCV frame with invalid candles removed."""
    if not isinstance(data, pd.DataFrame):
        raise TypeError("data must be a pandas DataFrame")

    frame = data.copy()
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.set_index("date")
    elif not isinstance(frame.index, pd.DatetimeIndex):
        try:
            frame.index = pd.to_datetime(frame.index, errors="raise")
        except (TypeError, ValueError) as exc:
            raise ValueError("A datetime date column or index is required") from exc

    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing OHLCV columns: {', '.join(missing)}")

    numeric_columns = list(REQUIRED_COLUMNS)
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=numeric_columns)
    frame = frame[~frame.index.isna()]
    frame = frame.sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    frame = frame[(frame["high"] >= frame["low"]) & (frame["volume"] >= 0)]
    frame.index = frame.index.normalize()
    if symbol is not None:
        frame.attrs["symbol"] = symbol
    frame.attrs["corporate_action_adjusted"] = corporate_action_adjusted
    return frame


class HistoricalDataLoader:
    """Loads and cleans daily OHLCV data from CSV files or in-memory frames."""

    def load_csv(self, path: str | Path, symbol: str | None = None) -> pd.DataFrame:
        return clean_historical_data(pd.read_csv(path), symbol=symbol)

    def load_frames(
        self,
        frames: dict[str, pd.DataFrame] | Iterable[pd.DataFrame],
    ) -> dict[str, pd.DataFrame]:
        if isinstance(frames, dict):
            return {
                symbol: clean_historical_data(frame, symbol=symbol)
                for symbol, frame in frames.items()
            }
        return {
            getattr(frame, "attrs", {}).get("symbol", f"symbol_{index}"): clean_historical_data(
                frame, symbol=getattr(frame, "attrs", {}).get("symbol")
            )
            for index, frame in enumerate(frames)
        }

