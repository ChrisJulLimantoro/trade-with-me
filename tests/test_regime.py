"""Tests for regime detection pure compute."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ats.processing.regime import compute_regime


def _make_btc_1h(n: int, trend: str = "flat", seed: int = 42) -> pd.DataFrame:
    """Create synthetic BTC 1h DataFrame with specified trend direction."""
    rng = np.random.default_rng(seed)
    if trend == "bull":
        drift = 0.005  # ~0.5% per bar → strong bull
    elif trend == "bear":
        drift = -0.005
    else:
        drift = 0.0
    noise = rng.standard_normal(n) * 0.002
    log_returns = drift + noise
    closes = 50000.0 * np.exp(np.cumsum(log_returns))
    highs = closes * 1.002
    lows = closes * 0.998
    opens = closes * (1 - log_returns)
    times = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "open_time": times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": np.full(n, 1000.0),
            "taker_buy_vol": np.full(n, 600.0),
        }
    )


def test_bull_regime_on_rising_bars() -> None:
    """Strong bull drift → trend='bull'."""
    df = _make_btc_1h(800, trend="bull")
    regime = compute_regime(df)
    assert regime["trend"] == "bull", f"Expected 'bull', got {regime['trend']}"


def test_bear_regime_on_falling_bars() -> None:
    """Strong bear drift → trend='bear'."""
    df = _make_btc_1h(800, trend="bear")
    regime = compute_regime(df)
    assert regime["trend"] == "bear", f"Expected 'bear', got {regime['trend']}"


def test_side_regime_with_small_slope() -> None:
    """Manually constructed flat EMA → trend='side'."""
    # Build a perfectly flat series so EMA slope is exactly 0
    n = 800
    flat_close = 50000.0
    times = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {
            "open_time": times,
            "open": [flat_close] * n,
            "high": [flat_close * 1.001] * n,
            "low": [flat_close * 0.999] * n,
            "close": [flat_close] * n,
            "volume": [1000.0] * n,
            "taker_buy_vol": [600.0] * n,
        }
    )
    regime = compute_regime(df)
    assert regime["trend"] == "side", (
        f"Flat price series → slope=0 → expected 'side', got '{regime['trend']}' "
        f"(slope={regime['btc_ema_slope_30d']:.4f})"
    )


def test_regime_cell_format() -> None:
    """regime_cell must be '{trend}-{volatility}'."""
    df = _make_btc_1h(800, trend="bull")
    regime = compute_regime(df)
    trend = regime["trend"]
    volatility = regime["volatility"]
    assert regime["regime_cell"] == f"{trend}-{volatility}"


def test_volatility_is_high_or_low() -> None:
    """volatility field must be 'high' or 'low'."""
    df = _make_btc_1h(800, trend="flat")
    regime = compute_regime(df)
    assert regime["volatility"] in ("high", "low")


def test_hysteresis_carry_in_deadzone() -> None:
    """When vol_pct is in [0.4, 0.6], carry previous label."""
    df = _make_btc_1h(800, trend="flat")
    # We call compute_regime twice: second call carries 'high' from first
    regime1 = compute_regime(df, prev_volatility="high")
    # If vol_pct landed in [0.4,0.6], volatility should be 'high' (carried)
    vol_pct = regime1["vol_percentile_180d"]
    if 0.4 <= vol_pct <= 0.6:
        assert regime1["volatility"] == "high", (
            "When in deadzone [0.4,0.6] with prev='high', should carry 'high'"
        )


def test_min_bars_required() -> None:
    """compute_regime raises ValueError with fewer than 31 bars."""
    df = _make_btc_1h(30, trend="flat")
    with pytest.raises(ValueError, match="31"):
        compute_regime(df)


def test_regime_fields_present() -> None:
    """All required fields must be present in the output dict."""
    df = _make_btc_1h(800)
    regime = compute_regime(df)
    required = {
        "ts", "btc_ema_slope_30d", "btc_realized_vol_30d",
        "vol_percentile_180d", "trend", "volatility", "regime_cell",
    }
    assert required.issubset(set(regime.keys()))
