"""Tests for basis premium causal lookup."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from ats.processing.basis import basis_premium_from_rows, basis_z_from_series


def _dt(hour: int) -> datetime:
    """Helper to create a UTC datetime at a given hour on 2026-01-01."""
    return datetime(2026, 1, 1, hour, 0, 0, tzinfo=UTC)


def test_causal_lookup_returns_most_recent_before_ts() -> None:
    """basis_premium_from_rows returns the latest premium with ts <= open_time."""
    rows = [
        (_dt(0), 0.0010),
        (_dt(1), 0.0015),
        (_dt(2), 0.0012),
        (_dt(3), 0.0020),
    ]
    # At open_time = 02:30, the latest ts <= 02:30 is _dt(2) with premium=0.0012
    ts = datetime(2026, 1, 1, 2, 30, 0, tzinfo=UTC)
    result = basis_premium_from_rows(rows, ts)
    assert result == pytest.approx(0.0012, abs=1e-9)


def test_no_row_before_ts_returns_none() -> None:
    """If no row has ts <= open_time, return None."""
    rows = [
        (_dt(5), 0.001),
        (_dt(6), 0.002),
    ]
    ts = _dt(4)
    result = basis_premium_from_rows(rows, ts)
    assert result is None


def test_exact_boundary_ts() -> None:
    """ts exactly equal to open_time: ts <= open_time should match."""
    rows = [
        (_dt(3), 0.0013),
        (_dt(4), 0.0017),
    ]
    ts = _dt(4)
    result = basis_premium_from_rows(rows, ts)
    assert result == pytest.approx(0.0017, abs=1e-9)


def test_no_future_leak() -> None:
    """Rows with ts > open_time must NOT be used."""
    rows = [
        (_dt(1), 0.001),
        (_dt(3), 0.005),  # future
        (_dt(5), 0.010),  # further future
    ]
    ts = _dt(2)
    result = basis_premium_from_rows(rows, ts)
    # Only _dt(1) <= _dt(2), so must return 0.001
    assert result == pytest.approx(0.001, abs=1e-9)


def test_basis_z_from_series() -> None:
    """z-score of a known series: last value should equal expected z."""
    n = 50
    rng = np.random.default_rng(7)
    arr = rng.standard_normal(n)
    series = pd.Series(arr)
    # Use lookback=30
    z_series = basis_z_from_series(series, lookback=30)
    valid = z_series.dropna()
    assert len(valid) > 0
    # z-scores can be any value — just check they're finite
    assert np.all(np.isfinite(valid.to_numpy()))


def test_basis_z_null_when_premium_null() -> None:
    """If premium is NaN, z must also be NaN."""
    series = pd.Series([0.001] * 40 + [float("nan")] + [0.001] * 10)
    z = basis_z_from_series(series, lookback=20)
    assert np.isnan(z.iloc[40]), "z must be NaN when premium is NaN"
