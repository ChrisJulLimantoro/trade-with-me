"""Rolling percentile-rank normalization."""

from __future__ import annotations

import pandas as pd

# Lookback per timeframe (≈30 days of closed bars)
PR_LOOKBACK: dict[str, int] = {"15m": 2880, "1h": 720, "4h": 180}


def percentile_rank(series: pd.Series, lookback: int) -> pd.Series:
    """Rolling percentile rank over the previous `lookback` closed bars.

    Output ∈ [0, 1]. Current bar is never in its own window (closed='left').
    """
    return series.rolling(lookback, closed="left").rank(pct=True)
