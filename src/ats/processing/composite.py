"""Composite momentum indicator."""

from __future__ import annotations

import numpy as np

# macd_hist is in PRICE units, so its raw magnitude scales with the coin's price level: BTC on
# 15m is O(1-100), a sub-dollar alt is O(0.001). A FIXED absolute scale therefore (a) saturates
# the tanh to a near-binary sign bit on BTC and (b) collapses to a no-signal ~0.5 on cheap alts —
# i.e. the momentum signal is coin-specific. Normalizing macd_hist by atr_14 (also price units)
# makes the input dimensionless and comparable across coins; ATR_MACD_SCALE spreads the typical
# normalized range across tanh's responsive band. When atr is unavailable (None / <= 0) the
# legacy absolute-units scale is used, so existing pinned golden tests and any scale-free callers
# stay byte-for-byte unchanged.
MACD_SCALE: float = 1.0           # legacy absolute-units scale (macd_hist as-is)
ATR_MACD_SCALE: float = 2.0       # scale for the atr-normalized (dimensionless) macd_hist


def momentum_composite(
    rsi: float, macd_hist: float, roc_5: float, *, atr: float | None = None
) -> float:
    """Blend RSI, MACD histogram and ROC into a [0, 1] composite momentum score.

    rsi_n:  RSI in [30, 70] mapped to [0, 1]. Values outside this range are clamped.
    macd_n: tanh-normalized MACD histogram. When ``atr`` is given (and > 0), macd_hist is first
            divided by atr so the signal is scale/coin-invariant; otherwise the legacy
            absolute-units scale is used (preserves pinned golden behavior).
    roc_n:  tanh-normalized 5-bar Rate of Change.
    """
    rsi_n = (rsi - 30.0) / 40.0
    if atr is not None and atr > 0:
        macd_n = 0.5 + 0.5 * np.tanh((macd_hist / atr) * ATR_MACD_SCALE)
    else:
        macd_n = 0.5 + 0.5 * np.tanh(macd_hist * MACD_SCALE)
    roc_n = 0.5 + 0.5 * np.tanh(roc_5 * 10.0)
    return float(0.5 * rsi_n + 0.3 * macd_n + 0.2 * roc_n)
