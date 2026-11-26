import numpy as np
import pytest
from _harness import bars_frame, run

from algo_engine.strategy import (
    Action, ParamError, Strategy, available_strategies, create_strategy, register_strategy,
)
from algo_engine.strategy.ema_crossover import EmaCrossover

CFG = {"name": "ema_crossover", "params": {"fast": 2, "slow": 3}}
# fast=2, slow=3: EMAs warm up at index 2; down-trend then sharp rise then sharp fall
CLOSES = [10.0, 9, 8, 7, 8, 12, 16, 12, 6, 2]


def test_registry_creates_strategy_from_config():
    assert "ema_crossover" in available_strategies()
    s = create_strategy(CFG)
    assert isinstance(s, EmaCrossover) and (s.params.fast, s.params.slow) == (2, 3)


def test_defaults_apply_when_params_omitted():
    assert create_strategy({"name": "ema_crossover"}).params.as_dict() == {"fast": 12, "slow": 26}


@pytest.mark.parametrize("params", [
    {"fast": 5, "slow": 5}, {"fast": 10, "slow": 5}, {"fast": 1, "slow": 5},
    {"fast": "a", "slow": 5}, {"fast": 2.5, "slow": 5}, {"fast": 3, "bogus": 1},
])
def test_invalid_params_rejected(params):
    with pytest.raises(ParamError):
        create_strategy({"name": "ema_crossover", "params": params})


def test_unknown_strategy_and_config_keys_rejected():
    with pytest.raises(ParamError):
        create_strategy({"name": "nope"})
    with pytest.raises(ParamError):
        create_strategy({"name": "ema_crossover", "param": {}})


def test_new_strategy_registers_without_engine_changes():
    @register_strategy("_always_flat_test")
    class Flat(Strategy):
        def on_bar(self, bar, history, portfolio):
            return []

    assert isinstance(create_strategy({"name": "_always_flat_test"}), Flat)
    assert run(create_strategy({"name": "_always_flat_test"}), bars_frame(CLOSES)) == []


def test_signals_fire_on_exact_crossover_bars():
    frame = bars_frame(CLOSES)
    signals = run(create_strategy(CFG), frame)
    # fast EMA(2) alpha 2/3, slow EMA(3) alpha 1/2. diff<=0 through idx4, first >0 at idx5; <0 again at idx8.
    assert [(s.action, s.trading_date) for s in signals] == [
        (Action.ENTER_LONG, frame.trading_date[5]),
        (Action.EXIT_LONG, frame.trading_date[8]),
    ]


def test_no_signals_during_warmup():
    # a cross exists right at the start but EMAs are not warmed up yet
    assert run(create_strategy({"name": "ema_crossover", "params": {"fast": 2, "slow": 50}}),
               bars_frame([5, 4, 3, 9, 1, 8.0])) == []


def test_no_exit_while_flat():
    # rising from the first valid bar is not a cross, so no entry; the later down-cross has no position to exit
    assert run(create_strategy(CFG), bars_frame([1.0, 2, 3, 4, 5, 4, 3, 2, 1])) == []


def test_each_symbol_has_independent_state():
    a, b = bars_frame(CLOSES, fin_code=1), bars_frame(CLOSES[::-1], fin_code=2)
    both = a.merge(b, how="outer").sort_values(["trading_date", "fin_code"]).reset_index(drop=True)
    solo = run(create_strategy(CFG), a)
    mixed = [s for s in run(create_strategy(CFG), both) if s.fin_code == 1]
    assert [(s.action, s.trading_date) for s in solo] == [(s.action, s.trading_date) for s in mixed]


def test_strategy_signals_are_causal_truncation_test():
    """Signals for bars <= t are identical when the strategy only ever sees data up to t."""
    closes = list(100 + np.random.default_rng(3).normal(0, 3, 150).cumsum())
    frame = bars_frame(closes)
    full = [(s.action, s.trading_date) for s in run(create_strategy({"name": "ema_crossover", "params": {"fast": 3, "slow": 8}}), frame)]
    assert full, "fixture should produce signals"
    for t in range(len(frame)):
        part = run(create_strategy({"name": "ema_crossover", "params": {"fast": 3, "slow": 8}}), frame.iloc[: t + 1])
        got = [(s.action, s.trading_date) for s in part]
        assert got == [x for x in full if x[1] <= frame.trading_date[t]]


def test_truncation_test_catches_a_cheating_strategy():
    """A strategy that reads tomorrow's close gives different signals on a truncated run, so the test flags it."""
    from algo_engine.strategy import Signal
    from algo_engine.strategy.base import StrategyParams

    def make_cheater(frame):
        future = dict(zip(frame.trading_date, frame.close.shift(-1)))  # built from the data it will be run on

        class Cheater(Strategy):
            def on_bar(self, bar, history, portfolio):
                nxt = future[bar.trading_date]
                if nxt == nxt and nxt > bar.close and portfolio.position_qty(bar.fin_code) == 0:
                    return [Signal(bar.fin_code, bar.trading_date, Action.ENTER_LONG, "cheat")]
                return []

        return Cheater(StrategyParams())

    frame = bars_frame([1.0, 2, 3, 4, 3, 2, 1, 2])
    full = [(s.action, s.trading_date) for s in run(make_cheater(frame), frame)]
    mismatches = 0
    for t in range(len(frame)):
        prefix = frame.iloc[: t + 1]
        got = [(s.action, s.trading_date) for s in run(make_cheater(prefix), prefix)]
        mismatches += got != [x for x in full if x[1] <= frame.trading_date[t]]
    assert mismatches > 0
