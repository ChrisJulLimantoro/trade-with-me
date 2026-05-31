"""Tests that compute_features_frame is deterministic (same input → identical frame)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ats.processing.features import compute_features_frame


def _make_candles(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """Create a synthetic candle DataFrame for feature tests."""
    rng = np.random.default_rng(seed)
    closes = 50000.0 + np.cumsum(rng.standard_normal(n) * 100)
    highs = closes + rng.uniform(10, 200, n)
    lows = closes - rng.uniform(10, 200, n)
    opens = closes - rng.standard_normal(n) * 50
    volumes = rng.uniform(100, 5000, n)
    tbv = volumes * rng.uniform(0.3, 0.7, n)
    times = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "open_time": times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
            "taker_buy_vol": tbv,
        }
    )


def test_compute_features_is_deterministic() -> None:
    """Same candles input → byte-identical output DataFrame."""
    df = _make_candles(200)
    result1 = compute_features_frame(df.copy(), tf="15m")
    result2 = compute_features_frame(df.copy(), tf="15m")

    # Check numeric columns are identical
    num_cols = result1.select_dtypes(include="number").columns
    for col in num_cols:
        np.testing.assert_array_equal(
            result1[col].to_numpy(),
            result2[col].to_numpy(),
            err_msg=f"Column {col!r} differs between two calls on identical input",
        )


def test_compute_features_output_shape() -> None:
    """Output has same number of rows as input and expected columns."""
    df = _make_candles(200)
    result = compute_features_frame(df, tf="15m")
    assert len(result) == len(df)
    expected_cols = {
        "atr_14", "rsi_14", "macd", "obv", "cvd_30", "cvd_slope_10",
        "momentum_composite", "pr_atr", "pr_rsi", "pr_momentum",
    }
    assert expected_cols.issubset(set(result.columns))


def test_pr_columns_in_bounds() -> None:
    """All pr_* columns that are non-NaN must be in [0, 1]."""
    df = _make_candles(300)
    result = compute_features_frame(df, tf="15m")
    pr_cols = [c for c in result.columns if c.startswith("pr_")]
    for col in pr_cols:
        valid = result[col].dropna()
        if len(valid) > 0:
            assert (valid >= 0).all(), f"{col} has values < 0"
            assert (valid <= 1).all(), f"{col} has values > 1"


def test_no_look_ahead_pr_atr() -> None:
    """Inject an extreme spike at bar 100; verify it doesn't affect bars before 100."""
    df = _make_candles(200)
    df_spike = df.copy()
    # Make bar 100 have an extreme ATR (very wide range)
    df_spike.loc[100, "high"] = df_spike.loc[100, "close"] * 10.0

    result_normal = compute_features_frame(df.copy(), tf="15m")
    result_spike = compute_features_frame(df_spike, tf="15m")

    # pr_atr at bars before 100 should be unaffected by the spike at 100
    # (closed='left' means bar 100's ATR is NOT in window of bars <= 100)
    for i in range(50, 100):
        nr = float(result_normal["pr_atr"].iloc[i])
        sr = float(result_spike["pr_atr"].iloc[i])
        if not (np.isnan(nr) and np.isnan(sr)):
            assert nr == pytest.approx(sr, abs=1e-9), (
                f"Bar {i} pr_atr changed ({nr} vs {sr}) — look-ahead detected!"
            )
