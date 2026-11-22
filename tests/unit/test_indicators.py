import numpy as np
import pandas as pd
import pytest

from algo_engine.indicators import IncrementalEma, atr, bollinger, ema, rolling_returns, rsi, sma

S = pd.Series


def nan_mask(s):
    return s.isna().tolist()


def test_sma_hand_values_and_warmup():
    out = sma(S([1.0, 2, 3, 4, 5]), 3)
    assert nan_mask(out) == [True, True, False, False, False]
    assert out.iloc[2:].tolist() == [2.0, 3.0, 4.0]


def test_ema_is_sma_seeded():
    out = ema(S([1.0, 2, 3, 4, 5]), 3)  # alpha 0.5, seed = mean(1,2,3) = 2
    assert nan_mask(out) == [True, True, False, False, False]
    assert out.iloc[2:].tolist() == [2.0, 3.0, 4.0]


def test_incremental_ema_matches_vectorized():
    close = S(np.random.default_rng(0).normal(100, 5, 200))
    inc = IncrementalEma(10)
    values = [inc.update(x) for x in close]
    np.testing.assert_allclose(values, ema(close, 10).to_numpy(), equal_nan=True)


def test_rsi_hand_values():
    out = rsi(S([10.0, 11, 12, 11, 13]), 2)
    assert nan_mask(out) == [True, True, False, False, False]
    assert out.iloc[2] == 100.0 and out.iloc[3] == pytest.approx(50.0)
    assert out.iloc[4] == pytest.approx(100 - 100 / 6)


def test_rsi_flat_series_is_50():
    assert rsi(S([5.0] * 6), 2).iloc[-1] == 50.0


def test_atr_hand_values():
    high, low, close = S([10.0, 11, 12, 12]), S([8.0, 9, 10, 9]), S([9.0, 10, 11, 10])
    out = atr(high, low, close, 2)  # TR = [nan, 2, 2, 3]
    assert nan_mask(out) == [True, True, False, False]
    assert out.iloc[2] == 2.0 and out.iloc[3] == pytest.approx(2.5)


def test_bollinger_uses_population_std():
    out = bollinger(S([1.0, 2, 3]), 3, 2.0)
    std = (2 / 3) ** 0.5
    assert out["mid"].iloc[2] == 2.0
    assert out["upper"].iloc[2] == pytest.approx(2 + 2 * std)
    assert out["lower"].iloc[2] == pytest.approx(2 - 2 * std)
    assert out["mid"].iloc[:2].isna().all()


def test_rolling_returns():
    out = rolling_returns(S([100.0, 110, 121]), 2)
    assert nan_mask(out) == [True, True, False] and out.iloc[2] == pytest.approx(0.21)


@pytest.mark.parametrize("bad", [0, -1, 2.5, True])
def test_invalid_window_rejected(bad):
    with pytest.raises(ValueError):
        sma(S([1.0, 2.0]), bad)


def test_short_input_is_all_nan():
    assert rsi(S([1.0, 2.0]), 5).isna().all()
    assert atr(S([2.0]), S([1.0]), S([1.5]), 3).isna().all()


def _frame(n=120):
    rng = np.random.default_rng(1)
    close = pd.Series(100 + rng.normal(0, 1, n).cumsum())
    return pd.DataFrame({"close": close, "high": close + rng.random(n), "low": close - rng.random(n)})


INDICATORS = {
    "sma": lambda f: sma(f.close, 5),
    "ema": lambda f: ema(f.close, 5),
    "rsi": lambda f: rsi(f.close, 5),
    "atr": lambda f: atr(f.high, f.low, f.close, 5),
    "bollinger": lambda f: bollinger(f.close, 5)["upper"],
    "returns": lambda f: rolling_returns(f.close, 5),
}


@pytest.mark.parametrize("name", INDICATORS)
def test_indicators_are_causal(name):
    """The value at t computed on data up to t equals the value computed on the full series."""
    f, fn = _frame(), INDICATORS[name]
    full = fn(f)
    for t in range(len(f)):
        prefix = fn(f.iloc[: t + 1]).iloc[-1]
        if np.isnan(full.iloc[t]):
            assert np.isnan(prefix)
        else:
            assert prefix == pytest.approx(full.iloc[t])
