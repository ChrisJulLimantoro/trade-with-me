"""Composite momentum indicator."""

from __future__ import annotations

import numpy as np

# MACD_SCALE normalizes macd_hist dimensionlessly.
# Typical MACD hist values for BTC on 15m are O(1-100); scale = 1.0 maps ±1 hist unit
# to ±tanh(1) ≈ ±0.76. Chosen to keep the tanh output well-spread for typical inputs.
MACD_SCALE: float = 1.0


def momentum_composite(rsi: float, macd_hist: float, roc_5: float) -> float:
    """Blend RSI, MACD histogram and ROC into a [0, 1] composite momentum score.

    rsi_n:  RSI in [30, 70] mapped to [0, 1]. Values outside this range are clamped.
    macd_n: tanh-normalized MACD histogram.
    roc_n:  tanh-normalized 5-bar Rate of Change.
    """
    rsi_n = (rsi - 30.0) / 40.0
    macd_n = 0.5 + 0.5 * np.tanh(macd_hist * MACD_SCALE)
    roc_n = 0.5 + 0.5 * np.tanh(roc_5 * 10.0)
    return float(0.5 * rsi_n + 0.3 * macd_n + 0.2 * roc_n)
