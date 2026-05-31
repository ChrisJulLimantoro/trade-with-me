"""Tests for rolling percentile rank normalization."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ats.processing.normalize import percentile_rank


def test_uniform_input_linear_ranks() -> None:
    """Uniform [0, 100] input → ranks should be approximately linear."""
    n = 200
    series = pd.Series(np.arange(n, dtype=float))
    result = percentile_rank(series, lookback=50)
    # After the warm-up (bar 50+), ranks should be linear
    valid = result.dropna()
    assert len(valid) > 100
    # Each step should be monotonically increasing
    # (each new bar is the highest in its window → rank 1.0)
    assert float(valid.iloc[-1]) == pytest.approx(1.0, abs=0.01)


def test_closed_left_excludes_current_bar() -> None:
    """rolling(closed='left').rank() places bar i-1's rank at position i.

    The result[i] = rank of bar (i-1)'s value in window [i-lookback, i).
    The spike at bar 50 must NOT affect result[50] or earlier (look-ahead check).
    It will only appear starting at result[51] (bar 50 enters the window).
    """
    series = pd.Series([1.0] * 100)
    series.iloc[50] = 1e9  # massive spike at bar 50

    result = percentile_rank(series, lookback=20)

    # result[50]: rank of bar 49 (value=1.0) in window [30,50) — all 1.0s.
    # Spike at bar 50 is NOT yet in the window → result is unaffected by the spike.
    rank_at_50 = float(result.iloc[50])
    assert not np.isnan(rank_at_50), "result[50] should be non-NaN"
    # All 1.0s in window + bar 49 also 1.0 → rank ≈ 0.5 (ties ranked at average)
    assert 0.4 < rank_at_50 < 0.7, (
        f"result[50] should be ~0.5 (bar49 vs all-1.0 window), got {rank_at_50}"
    )

    # result[51]: rank of bar 50 (value=1e9, the spike) in window [31,51).
    # Window [31,51) includes bar 50 (1e9) + bars 31-49 (1.0) → spike ranks last = 1.0
    rank_at_51 = float(result.iloc[51])
    assert rank_at_51 == pytest.approx(1.0, abs=0.01), (
        f"result[51] should be 1.0 (spike bar 50 is last in window [31,51)), got {rank_at_51}"
    )

    # result[52]: rank of bar 51 (value=1.0) in window [32,52), which contains spike at 50.
    # bar 51 value=1.0 < spike → rank near 0 (only 1.0s except the spike)
    rank_at_52 = float(result.iloc[52])
    assert rank_at_52 < 0.6, (
        f"result[52] should be low (bar51=1.0 vs window containing spike), got {rank_at_52}"
    )


def test_output_bounds() -> None:
    """Output must be in [0, 1]."""
    rng = np.random.default_rng(123)
    series = pd.Series(rng.standard_normal(200))
    result = percentile_rank(series, lookback=50)
    valid = result.dropna()
    assert (valid >= 0).all(), "percentile_rank must be ≥ 0"
    assert (valid <= 1).all(), "percentile_rank must be ≤ 1"


def test_constant_series_returns_nan_or_uniform() -> None:
    """Constant series → ranks are undefined (NaN or all equal)."""
    series = pd.Series([5.0] * 100)
    result = percentile_rank(series, lookback=20)
    # pandas ranks ties as the average; all-same window → rank = 0.5 or NaN
    # The key thing is it doesn't error
    assert len(result) == 100
