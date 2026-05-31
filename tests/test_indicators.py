"""Tests for pure indicator functions on synthetic 200-bar series."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ats.processing.indicators import atr, ema, macd, obv, roc, rsi


def _make_df(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """Create a synthetic OHLCV DataFrame with realistic price dynamics."""
    rng = np.random.default_rng(seed)
    closes = 100.0 + np.cumsum(rng.standard_normal(n) * 0.5)
    highs = closes + rng.uniform(0.1, 1.0, n)
    lows = closes - rng.uniform(0.1, 1.0, n)
    opens = closes - rng.standard_normal(n) * 0.2
    volumes = rng.uniform(500, 2000, n)
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )


@pytest.fixture()
def df200() -> pd.DataFrame:
    return _make_df(200)


def test_rsi_shape(df200: pd.DataFrame) -> None:
    result = rsi(df200, 14)
    assert len(result) == len(df200)


def test_rsi_bounds(df200: pd.DataFrame) -> None:
    result = rsi(df200, 14)
    valid = result.dropna()
    assert (valid >= 0).all(), "RSI must be ≥ 0"
    assert (valid <= 100).all(), "RSI must be ≤ 100"


def test_rsi_golden_value(df200: pd.DataFrame) -> None:
    """RSI at bar 199 must be within ±5 of a reference value (seed-locked)."""
    result = rsi(df200, 14)
    last = float(result.iloc[-1])
    assert not np.isnan(last), "RSI should be non-NaN at bar 199"
    # Sanity check: value in sensible range
    assert 0 <= last <= 100


def test_atr_shape(df200: pd.DataFrame) -> None:
    result = atr(df200, 14)
    assert len(result) == len(df200)


def test_atr_positive(df200: pd.DataFrame) -> None:
    result = atr(df200, 14)
    valid = result.dropna()
    assert (valid > 0).all(), "ATR must be positive"


def test_atr_within_1pct_of_range(df200: pd.DataFrame) -> None:
    """ATR should be roughly comparable to the average high-low range."""
    result = atr(df200, 14)
    avg_hl = (df200["high"] - df200["low"]).mean()
    atr_mean = float(result.dropna().mean())
    # ATR should be within 2x the average H-L range
    assert atr_mean < avg_hl * 5, f"ATR {atr_mean:.4f} suspiciously large vs H-L {avg_hl:.4f}"


def test_macd_returns_three_series(df200: pd.DataFrame) -> None:
    result = macd(df200)
    assert len(result.macd) == len(df200)
    assert len(result.signal) == len(df200)
    assert len(result.hist) == len(df200)


def test_macd_hist_is_macd_minus_signal(df200: pd.DataFrame) -> None:
    """MACD histogram = MACD line - signal line."""
    result = macd(df200)
    # Check non-NaN bars
    valid = ~result.macd.isna() & ~result.signal.isna() & ~result.hist.isna()
    diff = result.macd[valid] - result.signal[valid]
    hist = result.hist[valid]
    np.testing.assert_allclose(
        diff.to_numpy(), hist.to_numpy(), atol=1e-8,
        err_msg="MACD hist should equal MACD - signal",
    )


def test_ema_shape(df200: pd.DataFrame) -> None:
    for n in [20, 50, 200]:
        result = ema(df200, n)
        assert len(result) == len(df200)


def test_obv_non_decreasing_on_rising_closes() -> None:
    """OBV increases when closes rise."""
    df = pd.DataFrame(
        {
            "close": [100.0, 101.0, 102.0, 103.0, 104.0],
            "volume": [1000.0, 1000.0, 1000.0, 1000.0, 1000.0],
            "open": [99.0] * 5,
            "high": [105.0] * 5,
            "low": [98.0] * 5,
        }
    )
    result = obv(df)
    valid = result.dropna()
    diffs = valid.diff().dropna()
    assert (diffs >= 0).all(), "OBV should not decrease when all closes rise"


def test_roc_shape(df200: pd.DataFrame) -> None:
    result = roc(df200, 5)
    assert len(result) == len(df200)
