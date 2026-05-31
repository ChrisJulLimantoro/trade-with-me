"""Tests for momentum_composite with pinned MACD_SCALE golden values."""

from __future__ import annotations

import numpy as np
import pytest

from ats.processing.composite import MACD_SCALE, momentum_composite


def test_macd_scale_pinned() -> None:
    """MACD_SCALE must be pinned at 1.0 (golden value freeze)."""
    assert pytest.approx(1.0) == MACD_SCALE, (
        f"MACD_SCALE changed from 1.0 to {MACD_SCALE}; update tests if intentional."
    )


def test_rsi30_boundary() -> None:
    """rsi=30 → rsi_n = 0.0; with macd_hist=0 → macd_n=0.5; roc_5=0 → roc_n=0.5.

    result = 0.5*0.0 + 0.3*0.5 + 0.2*0.5 = 0.0 + 0.15 + 0.10 = 0.25
    """
    result = momentum_composite(rsi=30.0, macd_hist=0.0, roc_5=0.0)
    assert result == pytest.approx(0.25, abs=1e-9)


def test_macd_hist_zero_boundary() -> None:
    """macd_hist=0 → macd_n=0.5 (tanh(0)=0, so 0.5+0.5*0=0.5)."""
    # rsi=50 → rsi_n=0.5; roc_5=0 → roc_n=0.5
    result = momentum_composite(rsi=50.0, macd_hist=0.0, roc_5=0.0)
    expected = 0.5 * 0.5 + 0.3 * 0.5 + 0.2 * 0.5  # = 0.25 + 0.15 + 0.10 = 0.50
    assert result == pytest.approx(expected, abs=1e-9)


def test_rsi70_boundary() -> None:
    """rsi=70 → rsi_n=1.0."""
    result = momentum_composite(rsi=70.0, macd_hist=0.0, roc_5=0.0)
    expected = 0.5 * 1.0 + 0.3 * 0.5 + 0.2 * 0.5  # = 0.5 + 0.15 + 0.10 = 0.75
    assert result == pytest.approx(expected, abs=1e-9)


def test_fully_bullish() -> None:
    """Very high RSI, strong positive MACD hist, positive ROC → near 1.0."""
    result = momentum_composite(rsi=70.0, macd_hist=100.0, roc_5=10.0)
    # rsi_n=1.0; macd_n≈1.0; roc_n≈1.0
    assert result > 0.9, f"Expected near 1.0 for fully bullish inputs, got {result}"


def test_fully_bearish() -> None:
    """Very low RSI, strong negative MACD hist, negative ROC → near 0."""
    result = momentum_composite(rsi=30.0, macd_hist=-100.0, roc_5=-10.0)
    # rsi_n=0.0; macd_n≈0.0; roc_n≈0.0
    assert result < 0.1, f"Expected near 0.0 for fully bearish inputs, got {result}"


def test_golden_value_with_scale_1() -> None:
    """Freeze golden value for MACD_SCALE=1.0: rsi=55, macd_hist=2.0, roc_5=1.0."""
    result = momentum_composite(rsi=55.0, macd_hist=2.0, roc_5=1.0)
    # rsi_n = (55-30)/40 = 0.625
    rsi_n = 0.625
    # macd_n = 0.5 + 0.5*tanh(2.0*1.0) = 0.5 + 0.5*0.96402758... ≈ 0.98201
    macd_n = 0.5 + 0.5 * float(np.tanh(2.0 * MACD_SCALE))
    # roc_n = 0.5 + 0.5*tanh(1.0*10) ≈ 0.5 + 0.5*1.0 = 1.0 (tanh(10)≈1.0)
    roc_n = 0.5 + 0.5 * float(np.tanh(1.0 * 10.0))
    expected = 0.5 * rsi_n + 0.3 * macd_n + 0.2 * roc_n
    assert result == pytest.approx(expected, abs=1e-9)
