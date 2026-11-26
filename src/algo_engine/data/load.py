"""Command line: create the bars table and load/refresh it from the source database.

    python -m algo_engine.data.load --start 2018-01-01 [--end 2026-09-21] [--fin-codes 1 2 3]

Without --fin-codes the current Nifty 50 members (index_companies_mapping) are loaded. Re-running replaces
the stored bars of those symbols. The source is read-only; only the local store is written.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from algo_engine.config.settings import DbSettings, StoreSettings
from algo_engine.data.pipeline import NIFTY50_INDEX_FIN_CODE, load_bars, refresh_dividends
from algo_engine.data.postgres_provider import PostgresMarketDataProvider, create_readonly_engine
from algo_engine.data.store import BarStore, create_store_engine


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=date.fromisoformat, default=date(2018, 1, 1))
    ap.add_argument("--end", type=date.fromisoformat, default=date.today())
    ap.add_argument("--fin-codes", type=int, nargs="+", help="explicit symbols (default: Nifty 50 members)")
    ap.add_argument("--index", type=int, default=NIFTY50_INDEX_FIN_CODE, help="calendar/universe index fin_code")
    ap.add_argument("--dividends-only", action="store_true",
                    help="rebuild only the dividends table from the stored bars; bars are not touched")
    ap.add_argument("--migrate-only", action="store_true", help="create the schema and tables, load nothing")
    args = ap.parse_args(argv)

    src, dst = DbSettings.from_env(), StoreSettings.from_env()
    if (src.host, src.port, src.database) == (dst.host, dst.port, dst.database):
        print("Refusing to run: the store points at the source database. Set ALGO_PG* to a local DB.",
              file=sys.stderr)
        return 2

    store = BarStore(create_store_engine(dst), dst.schema)
    store.migrate()
    print(f"Schema '{dst.schema}' ready.")
    if args.migrate_only:
        return 0

    provider = PostgresMarketDataProvider(create_readonly_engine(src))
    codes = args.fin_codes or provider.get_index_members(args.index)
    if args.dividends_only:
        div = refresh_dividends(provider, store, [c for c in codes if c != args.index])
        print(f"Stored {len(div)} dividends for {div['fin_code'].nunique()} symbols "
              f"({div['ex_date'].min()} -> {div['ex_date'].max()}).")
        return 0
    print(f"Loading {len(codes)} symbols {args.start} -> {args.end} ...")
    result = load_bars(provider, store, codes, args.start, args.end, args.index)

    print(f"run_id={result.run_id} rows={result.rows_loaded} fingerprint={result.fingerprint}")
    print(result.report.summary())
    if len(result.events):
        print("Adjustment events by status:", result.events["status"].value_counts().to_dict())
    print(f"Dividends stored: {len(result.dividends)}")
    if len(result.unexplained_jumps):
        print(f"Unexplained overnight jumps after adjustment: {len(result.unexplained_jumps)} (review)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
