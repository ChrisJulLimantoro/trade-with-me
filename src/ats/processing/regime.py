"""Regime detection — pure compute + DB upsert."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from ats.processing.indicators import ema, realized_vol
from ats.processing.normalize import percentile_rank

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Trend classification cut: |EMA-30 slope| must exceed this many %/day to read as a trend rather
# than chop. A FIXED cut (the legacy 0.5) bakes in one volatility era — 0.5%/day looks like a
# "trend" when BTC is calm but like "chop" when BTC is wild, so the same tape is labeled
# differently across regimes and every downstream gate that keys off ``regime_cell`` drifts with
# it. Instead, scale the cut by BTC's own realized vol: it equals the legacy 0.5 AT a reference
# daily vol and moves proportionally with vol, so "trend vs chop" is a constant signal-to-noise
# judgement. At the reference vol the classification is byte-identical to the legacy constant (no
# BTC re-baseline at typical vol); it only diverges when vol is unusually high or low.
_LEGACY_TREND_SLOPE_PCT_PER_DAY: float = 0.5
# BTC's typical 30d realized daily vol (%/day): ~50% annualized / sqrt(252) ≈ 3.15. The reference
# at which the adaptive threshold reproduces the legacy 0.5.
_REFERENCE_DAILY_VOL_PCT: float = 3.15
# Clamp the adaptive threshold so a vol spike/crash can't make every bar trend or every bar chop.
_MIN_TREND_SLOPE_PCT_PER_DAY: float = 0.25
_MAX_TREND_SLOPE_PCT_PER_DAY: float = 1.5


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

    # Vol-adaptive trend cut: annualized realized vol → daily %/day (÷ sqrt(252)), scaled so the
    # cut equals the legacy 0.5 at the reference vol, then clamped to a sane band. Falls back to
    # the reference (→ legacy 0.5) when realized vol is unavailable.
    daily_vol_pct = (current_rv / np.sqrt(252.0)) * 100.0 if current_rv > 0 else _REFERENCE_DAILY_VOL_PCT
    trend_slope_threshold = float(
        np.clip(
            _LEGACY_TREND_SLOPE_PCT_PER_DAY * (daily_vol_pct / _REFERENCE_DAILY_VOL_PCT),
            _MIN_TREND_SLOPE_PCT_PER_DAY,
            _MAX_TREND_SLOPE_PCT_PER_DAY,
        )
    )

    # Trend
    if slope_pct_per_day > trend_slope_threshold:
        trend = "bull"
    elif slope_pct_per_day < -trend_slope_threshold:
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
        # Diagnostic: the vol-adaptive cut used for this row (not persisted — the upsert ignores
        # unused bind params). Lets a run show WHY a bar was labeled trend vs chop.
        "trend_slope_threshold": trend_slope_threshold,
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
