"""Train/test split and rolling walk-forward windows (calendar dates, inclusive on both ends)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from algo_engine.config.settings import ConfigError


@dataclass(frozen=True)
class Fold:
    index: int
    train: tuple[date, date]
    test: tuple[date, date]


def train_test_split(start: date, end: date, test_start: date) -> Fold:
    """Train = [start, test_start - 1 day], test = [test_start, end]."""
    if not (start < test_start <= end):
        raise ConfigError(f"test_start {test_start} must be after start {start} and not after end {end}")
    return Fold(0, (start, test_start - timedelta(days=1)), (test_start, end))


def walk_forward(start: date, end: date, train_months: int, test_months: int) -> list[Fold]:
    """Rolling windows: train for `train_months`, test the next `test_months`, then shift by `test_months`.

    Test windows do not overlap. A trailing window that would run past `end` is dropped, never shortened.
    """
    if train_months < 1 or test_months < 1:
        raise ConfigError("train_months and test_months must be at least 1")
    folds: list[Fold] = []
    origin = pd.Timestamp(start)
    k = 0
    while True:
        train_start = origin + pd.DateOffset(months=k * test_months)
        test_start = train_start + pd.DateOffset(months=train_months)
        test_end = (test_start + pd.DateOffset(months=test_months)).date() - timedelta(days=1)
        if test_end > end:
            break
        folds.append(Fold(k, (train_start.date(), test_start.date() - timedelta(days=1)),
                          (test_start.date(), test_end)))
        k += 1
    if not folds:
        raise ConfigError(f"No walk-forward fold fits between {start} and {end} "
                          f"with train={train_months} and test={test_months} months")
    return folds
