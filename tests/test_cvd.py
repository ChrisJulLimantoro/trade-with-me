"""Tests for CVD (Cumulative Volume Delta) indicator."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ats.processing.indicators import cvd


def _make_candles(n: int, tbv_vals: list[float | None] | None = None) -> pd.DataFrame:
    """Create a synthetic candle DataFrame with optional taker_buy_vol overrides."""
    closes = [100.0 + i * 0.1 for i in range(n)]
    vols = [1000.0] * n
    if tbv_vals is None:
        tbv_vals = [float(v * 0.6) for v in vols]
    # Ensure same length
    tbv: list[float | None] = list(tbv_vals) + [None] * (n - len(tbv_vals))
    tbv = tbv[:n]
    return pd.DataFrame(
        {
            "open": [c - 0.05 for c in closes],
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": vols,
            "taker_buy_vol": tbv,
        }
    )


def test_cvd_identity() -> None:
    """cvd_30[i] == cvd_30[i-1] + (2*tbv[i] - vol[i]) for all non-null bars."""
    n = 100
    tbv_vals = [600.0] * n  # all non-null
    df = _make_candles(n, tbv_vals=tbv_vals)

    cvd_series, _ = cvd(df, 30)

    expected_delta = 2.0 * df["taker_buy_vol"] - df["volume"]

    for i in range(1, n):
        if not np.isnan(cvd_series.iloc[i]) and not np.isnan(cvd_series.iloc[i - 1]):
            diff = float(cvd_series.iloc[i]) - float(cvd_series.iloc[i - 1])
            exp = float(expected_delta.iloc[i])
            assert diff == pytest.approx(exp, abs=1e-8), (
                f"CVD identity failed at bar {i}: diff={diff}, expected delta={exp}"
            )


def test_cvd_slope_on_known_series() -> None:
    """cvd_slope_10 should be positive for steadily increasing CVD."""
    n = 50
    # Make taker_buy_vol > volume/2 so CVD delta is always positive
    tbv_vals = [800.0] * n  # delta = 2*800-1000 = 600 per bar → CVD increases
    df = _make_candles(n, tbv_vals=tbv_vals)
    cvd_series, slope = cvd(df, 30)

    # After the 10-bar warm-up, slope should be positive
    valid_slopes = slope.dropna()
    assert len(valid_slopes) > 0
    assert (valid_slopes > 0).all(), "Slope should be positive for monotone increasing CVD"


def test_cvd_null_when_tbv_null_in_window() -> None:
    """If any taker_buy_vol in the trailing 30-bar window is NULL, CVD must be NULL."""
    n = 60
    tbv_vals: list[float | None] = [600.0] * n
    tbv_vals[10] = None  # inject a null at bar 10

    df = _make_candles(n, tbv_vals=tbv_vals)
    cvd_series, _ = cvd(df, 30)

    # Bar 10 itself must be null (tbv is null)
    assert np.isnan(cvd_series.iloc[10]), "Bar with null tbv must have null CVD"

    # Bar 39 = bar 10 + 29 bars later: the window [9,39) contains bar 10 (null)
    assert np.isnan(cvd_series.iloc[39]), (
        "Bar 39's window [9,39) contains the null bar 10 → CVD must be null"
    )

    # Bar 41 = bar 10 + 31 bars later: window [11,41) no longer contains bar 10
    assert not np.isnan(cvd_series.iloc[41]), (
        "Bar 41's window [11,41) does not contain the null bar → CVD should be non-null"
    )


def test_cvd_all_null_tbv() -> None:
    """All taker_buy_vol null → all CVD values null."""
    n = 40
    tbv_vals: list[float | None] = [None] * n
    df = _make_candles(n, tbv_vals=tbv_vals)
    cvd_series, slope = cvd(df, 30)
    assert cvd_series.isna().all(), "All CVD must be null when all tbv is null"
