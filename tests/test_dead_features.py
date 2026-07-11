"""Tests for the previously-dead derivatives features and the NaN-aware safeguard.

Covers:
- compute_features_frame populating funding_rate / funding_z_30d (funding_df) and
  funding_divergence / _z_30d / peer_count (xvenue_df),
- the envelope pruning of None/NaN feature keys (_prune_missing),
- the rule engine surfacing rules anchored on missing/NaN features.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ats.engine.rule_engine import evaluate_setup
from ats.planning.create_plan import _prune_missing
from ats.processing.features import compute_features_frame


def _make_candles(n: int = 200, seed: int = 42) -> pd.DataFrame:
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


# ── A. funding_rate / funding_z_30d ────────────────────────────────────────────


def test_funding_columns_populated_when_funding_df_passed() -> None:
    candles = _make_candles(200)
    # 8h funding stamped before the candle window, so every bar has a prior rate.
    ftimes = pd.date_range("2025-12-15", periods=120, freq="8h", tz="UTC")
    funding = pd.DataFrame({"funding_time": ftimes, "rate": np.linspace(-0.0005, 0.0005, 120)})

    out = compute_features_frame(candles, tf="15m", funding_df=funding)

    assert out["funding_rate"].notna().any(), "funding_rate should be populated"
    # Last bar is well past the first funding stamp → forward-filled, non-null.
    assert not math.isnan(float(out["funding_rate"].iloc[-1]))


def test_funding_columns_nan_without_funding_df() -> None:
    out = compute_features_frame(_make_candles(60), tf="15m")
    assert out["funding_rate"].isna().all()
    assert out["funding_z_30d"].isna().all()


# ── B. cross-venue funding divergence ──────────────────────────────────────────


def test_divergence_columns_populated_when_xvenue_df_passed() -> None:
    candles = _make_candles(200)
    # Start well before the candle window so bars have >=90 prior 8h boundaries (the 30d
    # z-score lookback) and the z-score column is populated, not just the raw divergence.
    boundaries = pd.date_range("2025-11-01", periods=220, freq="8h", tz="UTC")
    rows = []
    for i, t in enumerate(boundaries):
        base = 0.0001 * math.sin(i / 5)
        rows += [
            {"exchange": "binance", "funding_time": t, "rate": base + 0.00005},
            {"exchange": "bybit", "funding_time": t, "rate": base},
            {"exchange": "okx", "funding_time": t, "rate": base - 0.00002},
            {"exchange": "hyperliquid", "funding_time": t, "rate": base + 0.00001},
        ]
    xvenue = pd.DataFrame(rows)

    out = compute_features_frame(candles, tf="15m", xvenue_df=xvenue)

    assert out["funding_divergence"].notna().any(), "divergence should be populated"
    # 3 peers present on every boundary → peer_count == 3 once a boundary exists.
    pc = out["funding_peer_count"].dropna()
    assert (pc == 3).any()
    # z-score needs >=2 history points; later bars should have it.
    assert out["funding_divergence_z_30d"].notna().any()


def test_divergence_nan_without_xvenue_df() -> None:
    out = compute_features_frame(_make_candles(60), tf="15m")
    assert out["funding_divergence"].isna().all()
    assert out["funding_peer_count"].isna().all()


def test_divergence_needs_two_peers() -> None:
    """A single peer → divergence is None (NaN); peer_count reflects the lone peer."""
    candles = _make_candles(120)
    boundaries = pd.date_range("2025-12-20", periods=60, freq="8h", tz="UTC")
    rows = []
    for t in boundaries:
        rows += [
            {"exchange": "binance", "funding_time": t, "rate": 0.0001},
            {"exchange": "bybit", "funding_time": t, "rate": 0.00005},  # only 1 peer
        ]
    out = compute_features_frame(candles, tf="15m", xvenue_df=pd.DataFrame(rows))
    assert out["funding_divergence"].isna().all()


def test_divergence_z_poisoned_by_misaligned_binance_ms() -> None:
    """Binance fundingTime ms offsets that don't match peer stamps split the pivot
    index and leave funding_divergence_z_30d empty — the live cross_venue death mode.
    """
    from datetime import timedelta

    candles = _make_candles(200)
    boundaries = pd.date_range("2025-11-01", periods=220, freq="8h", tz="UTC")
    rows = []
    for i, t in enumerate(boundaries):
        base = 0.0001 * math.sin(i / 5)
        # Every other boundary: binance carries +5ms → separate pivot row from peers.
        bt = t + timedelta(milliseconds=5) if i % 2 == 0 else t
        rows += [
            {"exchange": "binance", "funding_time": bt, "rate": base + 0.00005},
            {"exchange": "bybit", "funding_time": t, "rate": base},
            {"exchange": "okx", "funding_time": t, "rate": base - 0.00002},
        ]
    misaligned = compute_features_frame(candles, tf="15m", xvenue_df=pd.DataFrame(rows))
    assert misaligned["funding_divergence_z_30d"].isna().all()

    # Same data with aligned stamps → z populates.
    aligned_rows = []
    for i, t in enumerate(boundaries):
        base = 0.0001 * math.sin(i / 5)
        aligned_rows += [
            {"exchange": "binance", "funding_time": t, "rate": base + 0.00005},
            {"exchange": "bybit", "funding_time": t, "rate": base},
            {"exchange": "okx", "funding_time": t, "rate": base - 0.00002},
        ]
    aligned = compute_features_frame(
        candles, tf="15m", xvenue_df=pd.DataFrame(aligned_rows)
    )
    assert aligned["funding_divergence_z_30d"].notna().any()


# ── C1. envelope pruning ────────────────────────────────────────────────────────


def test_prune_missing_drops_none_and_nan() -> None:
    row = {
        "rsi_14": 55.0,
        "basis_premium": None,      # DB NULL
        "oi_delta_pct_1h": float("nan"),
        "macd": 12.3,
        "symbol": "BTCUSDT",        # non-numeric kept
    }
    pruned = _prune_missing(row)
    assert pruned == {"rsi_14": 55.0, "macd": 12.3, "symbol": "BTCUSDT"}
    assert "basis_premium" not in pruned
    assert "oi_delta_pct_1h" not in pruned


# ── C2. unresolved rule operands ────────────────────────────────────────────────


def _setup(hard=None, soft=None) -> dict:
    return {
        "entry_zone_low": 100.0,
        "entry_zone_high": 110.0,
        "hard_rules": hard or [],
        "soft_rules": soft or [],
    }


def test_eval_flags_missing_feature_operand() -> None:
    features = {"price": 105.0, "rsi_14": 60.0}  # no basis_premium
    setup = _setup(hard=[{"left": "basis_premium", "operator": ">", "right": 0.0}])
    ev = evaluate_setup(setup, features, soft_threshold=0.6)
    assert ev.detected is False  # unresolved operand fails closed
    assert "basis_premium:missing" in ev.unresolved_operands


def test_eval_flags_nan_feature_operand() -> None:
    features = {"price": 105.0, "basis_premium": float("nan")}  # present but NaN
    setup = _setup(soft=[{"left": "basis_premium", "operator": ">", "right": 0.0}])
    ev = evaluate_setup(setup, features, soft_threshold=0.6)
    assert "basis_premium:nan" in ev.unresolved_operands


def test_resolvable_rule_not_flagged() -> None:
    features = {"price": 105.0, "rsi_14": 60.0}
    setup = _setup(hard=[{"left": "rsi_14", "operator": ">", "right": 50.0}])
    ev = evaluate_setup(setup, features, soft_threshold=0.6)
    assert ev.detected is True
    assert ev.unresolved_operands == []
