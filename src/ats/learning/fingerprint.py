"""Numeric setup fingerprint — the vector episodic memory is retrieved by.

A fixed-length, direction-agnostic float vector summarising the market conditions of a
setup. Built from the already-normalised percentile-rank features plus a few squashed
scalars, so every component is bounded ~[0, 1] and cosine distance is meaningful without
an embedding model. Deterministic: same feature row → identical fingerprint.
"""

from __future__ import annotations

import math
from typing import Any

# Percentile-rank features are already in [0, 1]; these form the bulk of the fingerprint.
_PR_KEYS = (
    "pr_atr",
    "pr_rsi",
    "pr_vol_zscore",
    "pr_oi_delta",
    "pr_funding_imbalance",
    "pr_momentum",
    "pr_cvd_divergence",
)

# Total dimensions: the 7 pr_* features + rsi + macd-direction + momentum_composite
# + squashed funding-z + squashed basis-z.
FINGERPRINT_DIM = len(_PR_KEYS) + 5


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _get(row: dict[str, Any], key: str, default: float) -> float:
    v = row.get(key)
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _squash(row: dict[str, Any], key: str) -> float:
    """Map an unbounded z-score-like value into [0, 1] via tanh (0.5 = neutral/missing)."""
    v = row.get(key)
    if v is None:
        return 0.5
    try:
        return 0.5 + 0.5 * math.tanh(float(v))
    except (TypeError, ValueError):
        return 0.5


def build_fingerprint(
    feature_row: dict[str, Any], regime: dict[str, Any] | None = None
) -> list[float]:
    """Build the fixed-length setup fingerprint. ``regime`` is accepted for future use."""
    vec = [_clamp01(_get(feature_row, k, 0.5)) for k in _PR_KEYS]
    vec.append(_clamp01(_get(feature_row, "rsi_14", 50.0) / 100.0))
    vec.append(1.0 if _get(feature_row, "macd_hist", 0.0) > 0 else 0.0)
    vec.append(_clamp01(_get(feature_row, "momentum_composite", 0.5)))
    vec.append(_squash(feature_row, "funding_z_30d"))
    vec.append(_squash(feature_row, "basis_z_30d"))
    return vec


def to_vector_literal(fingerprint: list[float]) -> str:
    """Render a fingerprint as a pgvector text literal, e.g. ``[0.1,0.2,...]``."""
    return "[" + ",".join(f"{x:.6f}" for x in fingerprint) + "]"
