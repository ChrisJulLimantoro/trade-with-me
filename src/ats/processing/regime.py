"""Regime detection — pure compute + DB upsert."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from ats.processing.indicators import ema, realized_vol
from ats.processing.normalize import percentile_rank

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def compute_regime(
    btc_1h_df: pd.DataFrame,
    prev_volatility: str | None = None,
) -> dict[str, Any]:
    """Compute regime from BTC 1h candles DataFrame.

    btc_1h_df: DataFrame with columns [open, high, low, close, volume, open_time].
    prev_volatility: previous volatility label for hysteresis ('high' | 'low' | None).

    Returns dict with keys:
      ts, btc_ema_slope_30d, btc_realized_vol_30d, vol_percentile_180d,
      trend, volatility, regime_cell.
    """
    if len(btc_1h_df) < 31:
        raise ValueError(f"Need at least 31 BTC 1h bars, got {len(btc_1h_df)}")

    # EMA-30 slope over last 30 closed bars (%/day)
    ema30 = ema(btc_1h_df, 30)
    # Take the last 31 values: indices -31 to -1 (closed='left': last 30 closed = [i-30, i))
    ema_window = ema30.iloc[-31:-1].dropna()
    if len(ema_window) < 2:
        slope_pct_per_day = 0.0
    else:
        x = np.arange(len(ema_window), dtype=float)
        raw_slope = float(np.polyfit(x, ema_window.to_numpy(dtype=float), 1)[0])
        # Convert to %/day: raw_slope is per-bar (1h), 24 bars/day
        last_ema = float(ema_window.iloc[-1])
        slope_pct_per_day = (raw_slope * 24 / last_ema) * 100.0 if last_ema != 0 else 0.0

    # Realized vol (30d = 720 bars for 1h)
    rv_series = realized_vol(btc_1h_df, n=720)
    current_rv = float(rv_series.iloc[-1]) if not np.isnan(rv_series.iloc[-1]) else 0.0

    # Vol percentile over 180d = 4320 bars for 1h
    rv_pct_series = percentile_rank(rv_series, lookback=4320)
    last_pct = rv_pct_series.iloc[-1]
    vol_pct = float(last_pct) if not np.isnan(last_pct) else float("nan")

    # Trend
    if slope_pct_per_day > 0.5:
        trend = "bull"
    elif slope_pct_per_day < -0.5:
        trend = "bear"
    else:
        trend = "side"

    # Volatility with hysteresis
    if not np.isnan(vol_pct):
        if vol_pct > 0.6:
            volatility = "high"
        elif vol_pct < 0.4:
            volatility = "low"
        else:
            # Dead zone: carry previous label
            volatility = prev_volatility if prev_volatility is not None else "low"
    else:
        volatility = prev_volatility if prev_volatility is not None else "low"

    ts = btc_1h_df["open_time"].iloc[-1]
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()

    return {
        "ts": ts,
        "btc_ema_slope_30d": slope_pct_per_day,
        "btc_realized_vol_30d": current_rv,
        "vol_percentile_180d": vol_pct if not np.isnan(vol_pct) else 0.0,
        "trend": trend,
        "volatility": volatility,
        "regime_cell": f"{trend}-{volatility}",
    }


async def upsert_regime(session: AsyncSession, regime: dict[str, Any]) -> None:
    """Upsert one regimes row. ON CONFLICT (ts) DO UPDATE."""
    from sqlalchemy import text

    await session.execute(
        text("""
            INSERT INTO regimes
              (ts, btc_ema_slope_30d, btc_realized_vol_30d, vol_percentile_180d,
               trend, volatility, regime_cell)
            VALUES
              (:ts, :btc_ema_slope_30d, :btc_realized_vol_30d, :vol_percentile_180d,
               :trend, :volatility, :regime_cell)
            ON CONFLICT (ts) DO UPDATE SET
              btc_ema_slope_30d=EXCLUDED.btc_ema_slope_30d,
              btc_realized_vol_30d=EXCLUDED.btc_realized_vol_30d,
              vol_percentile_180d=EXCLUDED.vol_percentile_180d,
              trend=EXCLUDED.trend,
              volatility=EXCLUDED.volatility,
              regime_cell=EXCLUDED.regime_cell
        """),
        regime,
    )
    await session.commit()
