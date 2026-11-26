"""Persistent store for normalized, adjusted bars (Milestone 3c, ADR-0004).

Lives in a separate, writable PostgreSQL schema (default local `algo`), never in the source tables.
`replace_bars` swaps ALL stored bars of the given symbols in one transaction. A partial date refresh
is deliberately not offered: adjustment factors depend on the whole history of a symbol, so mixing
old and new rows would give an inconsistent price series.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from importlib import resources

import pandas as pd
from sqlalchemy import Engine, bindparam, create_engine, text

from algo_engine.config.settings import StoreSettings
from algo_engine.data.normalize import DataQualityReport

_STORED = ["fin_code", "trading_date", "open", "high", "low", "close", "volume", "prev_close", "adj_factor"]


def create_store_engine(settings: StoreSettings) -> Engine:
    return create_engine(settings.url(), pool_pre_ping=True)


def _records(df: pd.DataFrame) -> list[dict[str, object]]:
    """DataFrame rows as plain Python values, with NaN/NaT as None (SQL NULL)."""
    clean = df.astype(object).where(df.notna(), None)
    return clean.to_dict("records")


class BarStore:
    def __init__(self, engine: Engine, schema: str = "algo") -> None:
        self._engine = engine
        self._schema = StoreSettings(schema=schema).schema  # validates the identifier

    @classmethod
    def from_env(cls) -> BarStore:
        s = StoreSettings.from_env()
        return cls(create_store_engine(s), s.schema)

    def migrate(self) -> None:
        """Create the schema and tables if missing. Safe to run repeatedly."""
        files = sorted(f for f in resources.files("algo_engine.data").joinpath("migrations").iterdir()
                       if f.name.endswith(".sql"))
        with self._engine.begin() as conn:
            for f in files:
                sql = f.read_text().format(schema=self._schema)
                for stmt in (part.strip() for part in sql.split(";") if part.strip()):
                    conn.execute(text(stmt))

    def replace_bars(
        self,
        bars: pd.DataFrame,
        fin_codes: Sequence[int],
        events: pd.DataFrame,
        report: DataQualityReport,
        start: date,
        end: date,
        calendar_code: int,
        fingerprint: str,
        dividends: pd.DataFrame | None = None,
    ) -> int:
        """Replace the stored bars of `fin_codes` with `bars`; return the new run_id. All or nothing.

        With `dividends` (see `data/dividends.py`), the stored dividends of these symbols are replaced in the
        same transaction. `None` leaves them untouched.
        """
        s = self._schema
        codes = [int(c) for c in fin_codes]
        quality = {
            "rows_in": report.rows_in,
            "rows_out": report.rows_out,
            "rejected": report.reject_counts(),
            "duplicates_removed": report.duplicates_removed,
            "conflicting_duplicate_dates": report.conflicting_duplicate_dates,
            "missing_dates": {str(k): len(v) for k, v in report.missing_dates.items()},
        }
        rejected = report.rejected
        with self._engine.begin() as conn:
            run_id = conn.execute(
                text(
                    f"INSERT INTO {s}.load_runs (start_date, end_date, fin_codes, calendar_code, "
                    "fingerprint, rows_loaded, quality) "
                    "VALUES (:start, :end, :codes, :cal, :fp, :n, CAST(:q AS jsonb)) RETURNING run_id"
                ),
                {"start": start, "end": end, "codes": codes, "cal": int(calendar_code),
                 "fp": fingerprint, "n": len(bars), "q": json.dumps(quality)},
            ).scalar_one()
            conn.execute(text(f"DELETE FROM {s}.bars WHERE fin_code IN :codes")
                         .bindparams(bindparam("codes", expanding=True)), {"codes": codes})
            if len(bars):
                rows = _records(bars[_STORED])
                for r in rows:
                    r["run_id"] = run_id
                conn.execute(text(
                    f"INSERT INTO {s}.bars (fin_code, trading_date, open, high, low, close, volume, "
                    "prev_close, adj_factor, run_id) VALUES (:fin_code, :trading_date, :open, :high, "
                    ":low, :close, :volume, :prev_close, :adj_factor, :run_id)"), rows)
            if len(events):
                rows = _records(events)
                for r in rows:
                    r["run_id"] = run_id
                conn.execute(text(
                    f"INSERT INTO {s}.adjustment_events (run_id, fin_code, ex_date, kind, factor, status, "
                    "apply_date, observed_ratio, note) VALUES (:run_id, :fin_code, :ex_date, :kind, "
                    ":factor, :status, :apply_date, :observed_ratio, :note)"), rows)
            if len(rejected):
                rows = _records(rejected.rename(columns={
                    "open_price": "open", "high_price": "high", "low_price": "low",
                    "close_price": "close", "reject_reason": "reason"})[
                    ["fin_code", "trading_date", "reason", "open", "high", "low", "close"]])
                for r in rows:
                    r["run_id"] = run_id
                conn.execute(text(
                    f"INSERT INTO {s}.rejected_rows (run_id, fin_code, trading_date, reason, open, high, "
                    "low, close) VALUES (:run_id, :fin_code, :trading_date, :reason, :open, :high, "
                    ":low, :close)"), rows)
            if dividends is not None:
                self._replace_dividends(conn, codes, dividends)
        return int(run_id)

    def _replace_dividends(self, conn, codes: list[int], dividends: pd.DataFrame) -> None:
        conn.execute(text(f"DELETE FROM {self._schema}.dividends WHERE fin_code IN :codes")
                     .bindparams(bindparam("codes", expanding=True)), {"codes": codes})
        if len(dividends):
            conn.execute(text(
                f"INSERT INTO {self._schema}.dividends (fin_code, ex_date, amount, adj_factor, adj_amount, "
                "dividend_type) VALUES (:fin_code, :ex_date, :amount, :adj_factor, :adj_amount, :dividend_type)"),
                _records(dividends[["fin_code", "ex_date", "amount", "adj_factor", "adj_amount", "dividend_type"]]))

    def replace_dividends(self, fin_codes: Sequence[int], dividends: pd.DataFrame) -> None:
        """Replace the stored dividends of `fin_codes` without touching bars. All or nothing."""
        with self._engine.begin() as conn:
            self._replace_dividends(conn, [int(c) for c in fin_codes], dividends)

    def read_dividends(self, fin_codes: Sequence[int], start: date | None = None,
                       end: date | None = None) -> pd.DataFrame:
        """Dividends with `amount` per share of the adjusted series, sorted by (ex_date, fin_code)."""
        cols = ["fin_code", "ex_date", "amount"]
        if not fin_codes:
            return pd.DataFrame(columns=cols)
        stmt = text(
            f"SELECT fin_code, ex_date, adj_amount AS amount FROM {self._schema}.dividends "
            "WHERE fin_code IN :codes AND ex_date >= COALESCE(:start, DATE '-infinity') "
            "AND ex_date <= COALESCE(:end, DATE 'infinity') ORDER BY ex_date, fin_code, amount"
        ).bindparams(bindparam("codes", expanding=True))
        with self._engine.connect() as conn:
            df = pd.read_sql_query(stmt, conn, params={
                "codes": [int(c) for c in fin_codes], "start": start, "end": end})
        return df[cols]

    def read_bars(self, fin_codes: Sequence[int], start: date | None = None,
                  end: date | None = None) -> pd.DataFrame:
        """Stored (adjusted) bars, sorted by (fin_code, trading_date), with `adj_factor`."""
        if not fin_codes:
            return pd.DataFrame(columns=_STORED)
        stmt = text(
            f"SELECT {', '.join(_STORED)} FROM {self._schema}.bars "
            "WHERE fin_code IN :codes AND trading_date >= COALESCE(:start, DATE '-infinity') "
            "AND trading_date <= COALESCE(:end, DATE 'infinity') ORDER BY fin_code, trading_date"
        ).bindparams(bindparam("codes", expanding=True))
        with self._engine.connect() as conn:
            df = pd.read_sql_query(stmt, conn, params={
                "codes": [int(c) for c in fin_codes], "start": start, "end": end})
        return df[_STORED]

    def latest_run(self) -> dict[str, object] | None:
        stmt = text(f"SELECT run_id, loaded_at, start_date, end_date, fin_codes, fingerprint, "
                    f"rows_loaded, quality FROM {self._schema}.load_runs ORDER BY run_id DESC LIMIT 1")
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        return dict(row) if row else None

