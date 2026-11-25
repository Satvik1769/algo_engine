"""Run directory writer. Output has no wall-clock values, so identical inputs give identical bytes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from algo_engine.engine.loop import RunResult


def write_run(base_dir: Path, result: RunResult) -> Path:
    """Write config.json, events.jsonl, equity.csv, trades.csv and manifest.json to `base_dir/<run_id>/`."""
    out = base_dir / result.run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.json").write_text(result.config.snapshot_json(), encoding="utf-8", newline="\n")
    lines = (json.dumps(e.to_dict(), sort_keys=True) for e in result.events)
    (out / "events.jsonl").write_text("".join(f"{line}\n" for line in lines), encoding="utf-8", newline="\n")
    result.equity.to_csv(out / "equity.csv", index=False, lineterminator="\n")
    trade_columns = ["fin_code", "entry_date", "exit_date", "quantity", "entry_price", "exit_price",
                     "gross_pnl", "costs", "net_pnl", "holding_days"]
    pd.DataFrame([vars(t) for t in result.trades], columns=trade_columns).to_csv(
        out / "trades.csv", index=False, lineterminator="\n")
    manifest = {
        "run_id": result.run_id,
        "data_fingerprint": result.data_fingerprint,
        "price_basis": result.config.price_basis,
        "calendar_days": result.calendar_days,
        "first_date": result.equity["trading_date"].iloc[0].isoformat(),
        "last_date": result.equity["trading_date"].iloc[-1].isoformat(),
        "events": len(result.events),
        "signals": len(result.signals),
        "trades": len(result.trades),
        "seed": result.config.seed,
        "slippage_bps": result.config.slippage_bps,
        "cost_schedules": [c.effective_from.isoformat() for c in result.config.costs],
        "zero_cost_diagnostic": all(c.is_zero_cost for c in result.config.costs),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n",
                                       encoding="utf-8", newline="\n")
    return out
