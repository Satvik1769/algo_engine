"""Experiment runner: grid search with a registry, optional train/test split or walk-forward, robustness checks.

Selection rule: on each training window the combination with the highest objective (default Sharpe) is selected.
Test windows are only used to evaluate that selection; they never influence it. Every window is a separate
backtest that starts cold (fresh capital, indicators warmed up only from the window's own bars), so results near
the start of a test window reflect the warm-up gap.

Modes
  in_sample     grid on the whole [start, end]; there is no out-of-sample evidence (labelled as such)
  split         grid on [start, test_start), evaluate every combination and the selection on [test_start, end]
  walk_forward  rolling train/test folds; only the selection of each fold is run on its test window
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from algo_engine.analytics.metrics import TRADING_DAYS, sharpe_ratio
from algo_engine.analytics.report import _clean
from algo_engine.config.settings import ConfigError
from algo_engine.engine.config import RunConfig
from algo_engine.engine.loop import (
    data_fingerprint, dividends_fingerprint, prepare_bars, prepare_dividends,
)
from algo_engine.research.grid import (
    Trial, make_config, period_sharpe, run_grid, run_trial, select_best,
)
from algo_engine.research.heatmap import heatmap_svg
from algo_engine.research.robustness import deflated_sharpe, scale_costs, trade_monte_carlo
from algo_engine.research.splits import Fold, train_test_split, walk_forward

OBJECTIVES = ("sharpe", "cagr", "total_return", "calmar")
REGISTRY_COLUMNS = ["fold", "phase", "selected", "overrides", "run_id", "start", "end", "sharpe", "cagr",
                    "total_return", "max_drawdown", "calmar", "trades", "final_equity", "days"]


def _row(fold: int, phase: str, selected: bool, trial: Trial) -> dict:
    return {"fold": fold, "phase": phase, "selected": selected, "overrides": json.dumps(trial.overrides, sort_keys=True),
            "run_id": trial.run_id, "start": trial.config.start.isoformat(), "end": trial.config.end.isoformat(),
            **{k: trial.metrics[k] for k in ("sharpe", "cagr", "total_return", "max_drawdown", "calmar", "trades",
                                             "final_equity", "days")}}


def experiment_id(base: RunConfig, grid: dict, mode: dict, rf: float, objective: str, bars: pd.DataFrame,
                  dividends: pd.DataFrame | None) -> str:
    fp = data_fingerprint(prepare_bars(base, bars))
    dfp = dividends_fingerprint(prepare_dividends(base, dividends))
    text = json.dumps({"base": base.snapshot_json(), "grid": grid, "mode": mode, "rf": rf, "objective": objective,
                       "bars": fp, "dividends": dfp}, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def run_experiment(base: RunConfig, grid: dict[str, list], bars: pd.DataFrame, dividends: pd.DataFrame | None = None,
                   *, test_start: date | None = None, walk: tuple[int, int] | None = None, rf: float = 0.065,
                   objective: str = "sharpe", n_mc: int = 2000,
                   stress_cost: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0),
                   stress_slippage_bps: tuple[float, ...] = (0.0, 5.0, 10.0, 20.0),
                   out_dir: str | Path | None = None) -> dict:
    if objective not in OBJECTIVES:
        raise ConfigError(f"objective must be one of {OBJECTIVES}, got {objective!r}")
    if test_start is not None and walk is not None:
        raise ConfigError("Use either a train/test split or walk-forward, not both")
    if walk is not None:
        mode = "walk_forward"
        folds = walk_forward(base.start, base.end, walk[0], walk[1])
    elif test_start is not None:
        mode = "split"
        folds = [train_test_split(base.start, base.end, test_start)]
    else:
        mode = "in_sample"
        folds = [Fold(0, (base.start, base.end), (base.end, base.end))]
    mode_spec = {"mode": mode, "test_start": test_start, "walk": walk}
    exp_id = experiment_id(base, grid, mode_spec, rf, objective, bars, dividends)

    registry: list[dict] = []
    fold_out: list[dict] = []
    heatmaps: dict[str, str] = {}
    pooled_returns: list[np.ndarray] = []
    pooled_pnl: list[np.ndarray] = []
    last_selected_test: Trial | None = None

    for fold in folds:
        train_trials, invalid = run_grid(base, grid, bars, dividends, fold.train[0], fold.train[1], rf)
        if not train_trials:
            raise ConfigError(f"No valid parameter combination in the grid (invalid: {len(invalid)})")
        best_i = select_best(train_trials, objective)
        best = train_trials[best_i]
        for i, t in enumerate(train_trials):
            registry.append(_row(fold.index, "train", i == best_i, t))
        entry: dict = {
            "fold": fold.index, "train": [d.isoformat() for d in fold.train],
            "valid_trials": len(train_trials), "invalid_combinations": len(invalid),
            "selected": best.overrides, "train_metrics": best.metrics,
            "deflated_sharpe": deflated_sharpe(best.returns, [period_sharpe(t.returns, rf) for t in train_trials], rf),
        }
        _add_heatmap(heatmaps, f"train_fold{fold.index}", f"{objective} (train)", train_trials, grid, objective)
        if mode != "in_sample":
            entry["test"] = [d.isoformat() for d in fold.test]
            if mode == "split":
                test_trials, _ = run_grid(base, grid, bars, dividends, fold.test[0], fold.test[1], rf)
                for t in test_trials:
                    registry.append(_row(fold.index, "test", t.overrides == best.overrides, t))
                sel = next(t for t in test_trials if t.overrides == best.overrides)
                entry["rank_correlation_train_vs_test"] = _rank_corr(train_trials, test_trials, objective)
                _add_heatmap(heatmaps, "test", f"{objective} (test)", test_trials, grid, objective)
            else:
                cfg = make_config(base, best.overrides, fold.test[0], fold.test[1])
                sel = run_trial(cfg, bars, dividends, best.overrides, rf)
                registry.append(_row(fold.index, "test", True, sel))
            entry["test_metrics"] = sel.metrics
            pooled_returns.append(sel.returns)
            pooled_pnl.append(sel.net_pnl)
            last_selected_test = sel
        fold_out.append(entry)

    summary: dict = {
        "experiment_id": exp_id, "mode": mode, "objective": objective, "risk_free_rate": rf,
        "base_config": base.to_dict(), "grid": grid, "folds": fold_out,
        "total_trials": sum(f["valid_trials"] for f in fold_out),
        "warnings": _warnings(mode, fold_out, base),
    }
    evaluated = last_selected_test if mode != "in_sample" else _full_selected(registry, base, bars, dividends, fold_out, rf)
    if mode == "walk_forward":
        pr = np.concatenate(pooled_returns)
        summary["pooled_out_of_sample"] = {
            "days": int(len(pr)), "sharpe": sharpe_ratio(pr, rf), "total_return": float(np.prod(1 + pr) - 1),
            "note": "daily returns of all test windows joined; each window restarts with the initial capital"}
        pnl = np.concatenate(pooled_pnl) if pooled_pnl else np.array([])
        summary["trade_monte_carlo"] = trade_monte_carlo(pnl, base.initial_capital, n_mc, base.seed)
    elif evaluated is not None:
        summary["evaluated_window"] = [evaluated.config.start.isoformat(), evaluated.config.end.isoformat()]
        summary["trade_monte_carlo"] = trade_monte_carlo(evaluated.net_pnl, base.initial_capital, n_mc, base.seed)
        summary["stress"] = _stress(evaluated.config, bars, dividends, stress_cost, stress_slippage_bps, rf)

    summary = _clean(summary)
    if out_dir is not None:
        summary["output_dir"] = str(_write(Path(out_dir) / exp_id, summary, registry, heatmaps))
    return summary


def _full_selected(registry, base, bars, dividends, fold_out, rf) -> Trial | None:
    sel = fold_out[0]["selected"]
    return run_trial(make_config(base, sel), bars, dividends, sel, rf)


def _stress(config: RunConfig, bars, dividends, cost_mults, slip_values, rf) -> list[dict]:
    rows = []
    for cm in cost_mults:
        for sb in slip_values:
            cfg = replace(scale_costs(config, cm), slippage_bps=float(sb))
            t = run_trial(cfg, bars, dividends, {}, rf)
            rows.append({"cost_multiplier": cm, "slippage_bps": sb, "sharpe": t.metrics["sharpe"],
                         "total_return": t.metrics["total_return"], "max_drawdown": t.metrics["max_drawdown"],
                         "trades": t.metrics["trades"]})
    return rows


def _rank_corr(train: list[Trial], test: list[Trial], objective: str) -> float | None:
    by_test = {json.dumps(t.overrides, sort_keys=True): t.metrics[objective] for t in test}
    pairs = [(t.metrics[objective], by_test.get(json.dumps(t.overrides, sort_keys=True))) for t in train]
    pairs = [(a, b) for a, b in pairs if b is not None and not (math.isnan(a) or math.isnan(b))]
    if len(pairs) < 3:
        return None
    a, b = pd.Series([p[0] for p in pairs]), pd.Series([p[1] for p in pairs])
    c = a.rank().corr(b.rank())  # Spearman: Pearson on ranks (average ranks for ties)
    return None if math.isnan(c) else float(c)


def _add_heatmap(out: dict, name: str, title: str, trials: list[Trial], grid: dict, objective: str) -> None:
    axes = list(grid)
    if len(axes) < 2:
        return
    xk, yk = axes[0], axes[1]
    cells: dict[tuple, float] = {}
    for t in trials:
        key = (t.overrides[xk], t.overrides[yk])
        v = t.metrics[objective]
        if key not in cells or (not math.isnan(v) and (math.isnan(cells[key]) or v > cells[key])):
            cells[key] = v
    note = "" if len(axes) == 2 else f" (best over {', '.join(axes[2:])})"
    out[name] = heatmap_svg(title + note, xk, yk, list(grid[xk]), list(grid[yk]), cells)


def _warnings(mode: str, folds: list[dict], base: RunConfig) -> list[str]:
    w = ["Each window is a separate cold-start backtest with fresh capital.",
         "Trial count and the deflated Sharpe cover only the combinations in this grid, not earlier research."]
    if mode == "in_sample":
        w.insert(0, "IN-SAMPLE ONLY: the selected parameters were chosen on the same data they are scored on.")
    for f in folds:
        d = f["deflated_sharpe"]
        if d.get("dsr") is None:
            w.append(f"Fold {f['fold']}: deflated Sharpe undefined ({d.get('reason')}).")
        elif d["dsr"] < 0.95:
            w.append(f"Fold {f['fold']}: deflated Sharpe {d['dsr']:.2f} is below 0.95, so the selected result is "
                     "not clearly better than the best of this many unskilled trials.")
    w.append("Survivorship: the symbols are today's constituents.")
    return w


def _write(path: Path, summary: dict, registry: list[dict], heatmaps: dict[str, str]) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "summary.json").write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n", encoding="utf-8",
                                      newline="\n")
    pd.DataFrame(registry, columns=REGISTRY_COLUMNS).to_csv(path / "registry.csv", index=False, lineterminator="\n")
    for name, svg in heatmaps.items():
        (path / f"heatmap_{name}.svg").write_text(svg, encoding="utf-8", newline="\n")
    return path


def format_summary(s: dict) -> str:
    def f(x, pct=False):
        return "n/a" if x is None else (f"{x * 100:.2f}%" if pct else f"{x:.2f}")

    lines = [f"experiment {s['experiment_id']}  mode={s['mode']}  objective={s['objective']}  "
             f"trials={s['total_trials']}"]
    for fo in s["folds"]:
        tm, d = fo["train_metrics"], fo["deflated_sharpe"]
        lines.append(f"  fold {fo['fold']}  train {fo['train'][0]}..{fo['train'][1]}  valid={fo['valid_trials']} "
                     f"invalid={fo['invalid_combinations']}")
        lines.append(f"    selected {fo['selected']}  train Sharpe {f(tm['sharpe'])}  CAGR {f(tm['cagr'], True)}  "
                     f"maxDD {f(tm['max_drawdown'], True)}  deflated Sharpe {f(d.get('dsr'))}")
        if "test_metrics" in fo:
            m = fo["test_metrics"]
            lines.append(f"    test {fo['test'][0]}..{fo['test'][1]}  Sharpe {f(m['sharpe'])}  CAGR {f(m['cagr'], True)}  "
                         f"maxDD {f(m['max_drawdown'], True)}  trades {int(m['trades'])}")
        if fo.get("rank_correlation_train_vs_test") is not None or "rank_correlation_train_vs_test" in fo:
            lines.append(f"    rank correlation train vs test {f(fo.get('rank_correlation_train_vs_test'))}")
    if "pooled_out_of_sample" in s:
        p = s["pooled_out_of_sample"]
        lines.append(f"  pooled out-of-sample: {p['days']} days  Sharpe {f(p['sharpe'])}  total return {f(p['total_return'], True)}")
    mc = s.get("trade_monte_carlo")
    if mc:
        lines.append(f"  trade Monte Carlo ({mc['trades']} trades): max drawdown actual {f(mc['actual_max_drawdown'], True)}, "
                     f"shuffled p5/p50/p95 {f(mc['shuffled_max_drawdown']['p5'], True)}/"
                     f"{f(mc['shuffled_max_drawdown']['p50'], True)}/{f(mc['shuffled_max_drawdown']['p95'], True)}; "
                     f"bootstrap P&L positive in {mc['share_bootstrap_total_pnl_positive']:.0%} of resamples")
    if s.get("stress"):
        lines.append("  stress (cost multiplier x slippage bps -> Sharpe / total return):")
        for r in s["stress"]:
            lines.append(f"    x{r['cost_multiplier']:g} costs, {r['slippage_bps']:g} bps: Sharpe {f(r['sharpe'])}  "
                         f"return {f(r['total_return'], True)}  trades {int(r['trades'])}")
    lines += [f"  ! {w}" for w in s["warnings"]]
    return "\n".join(lines)
