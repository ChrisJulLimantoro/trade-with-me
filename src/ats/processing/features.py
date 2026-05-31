"""Feature orchestrator — compute + upsert features rows."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from sqlalchemy import text

from ats.processing.composite import momentum_composite
from ats.processing.indicators import atr, cvd, ema, macd, obv, realized_vol, roc, rsi
from ats.processing.normalize import PR_LOOKBACK, percentile_rank

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def compute_features_frame(
    candles_df: pd.DataFrame,
    *,
    tf: str,
    funding_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Pure: compute all features for a (symbol, tf) candle DataFrame.

    candles_df must have columns: open, high, low, close, volume, taker_buy_vol, open_time.
    funding_df (optional): DataFrame with columns [funding_time, rate] for funding_z.

    Returns a DataFrame aligned to candles_df index with all feature columns.
    xvenue and basis columns are filled with NaN (require DB; computed in DB wrapper).
    """
    df = candles_df.reset_index(drop=True)
    n = len(df)

    # ── Raw indicators ────────────────────────────────────────────────────────
    atr_14 = atr(df, 14).reset_index(drop=True)
    atr_pct = (atr_14 / df["close"]).rename("atr_pct")

    rsi_14 = rsi(df, 14).reset_index(drop=True)
    ema_20 = ema(df, 20).reset_index(drop=True)
    ema_50 = ema(df, 50).reset_index(drop=True)
    ema_200 = ema(df, 200).reset_index(drop=True)

    macd_res = macd(df)

    obv_series = obv(df).reset_index(drop=True)

    # Vol z-score (20-bar)
    vol_mean = df["volume"].rolling(20, closed="left").mean()
    vol_std = df["volume"].rolling(20, closed="left").std()
    vol_zscore_20 = ((df["volume"] - vol_mean) / vol_std).rename("vol_zscore_20")

    # Realized vol
    rv30 = realized_vol(df, 30).rename("realized_vol_30d").reset_index(drop=True)

    # ROC (for composite)
    roc_5 = roc(df, 5).reset_index(drop=True)

    # ── CVD ──────────────────────────────────────────────────────────────────
    cvd_30, cvd_slope_10 = cvd(df, 30)
    cvd_30 = cvd_30.reset_index(drop=True)
    cvd_slope_10 = cvd_slope_10.reset_index(drop=True)

    # ── Funding rate + z-score ────────────────────────────────────────────────
    funding_rate_col = pd.Series([float("nan")] * n, dtype=float)
    funding_z_col = pd.Series([float("nan")] * n, dtype=float)
    if funding_df is not None and not funding_df.empty:
        # Merge funding onto each candle bar via asof (last funding_time <= open_time)
        fund = funding_df.sort_values("funding_time").reset_index(drop=True)
        for i, row in df.iterrows():
            ot = row["open_time"]
            if isinstance(ot, pd.Timestamp):
                ot = ot.to_pydatetime()
            match = fund[fund["funding_time"] <= ot]
            if not match.empty:
                funding_rate_col.iloc[i] = float(match.iloc[-1]["rate"])
        # z-score of funding rate over trailing 30d
        lookback = PR_LOOKBACK.get(tf, 720)
        fz_mean = funding_rate_col.rolling(lookback, closed="left").mean()
        fz_std = funding_rate_col.rolling(lookback, closed="left").std()
        funding_z_col = ((funding_rate_col - fz_mean) / fz_std).rename("funding_z_30d")

    # ── OI delta ─────────────────────────────────────────────────────────────
    # Placeholder — OI delta requires join with open_interest table; filled by DB wrapper
    oi_delta_1h = pd.Series([float("nan")] * n, dtype=float)
    oi_delta_24h = pd.Series([float("nan")] * n, dtype=float)

    # ── Composite momentum ────────────────────────────────────────────────────
    momentum_vals: list[float] = []
    for i in range(n):
        r = float(rsi_14.iloc[i]) if not np.isnan(rsi_14.iloc[i]) else float("nan")
        m = float(macd_res.hist.iloc[i]) if not np.isnan(macd_res.hist.iloc[i]) else float("nan")
        rc = float(roc_5.iloc[i]) if not np.isnan(roc_5.iloc[i]) else float("nan")
        if any(np.isnan(v) for v in [r, m, rc]):
            momentum_vals.append(float("nan"))
        else:
            momentum_vals.append(momentum_composite(r, m, rc))
    momentum_col = pd.Series(momentum_vals, dtype=float)

    # ── Percentile ranks ─────────────────────────────────────────────────────
    lookback = PR_LOOKBACK.get(tf, 720)
    pr_atr = percentile_rank(atr_14, lookback)
    pr_rsi = percentile_rank(rsi_14, lookback)
    pr_vol_zscore = percentile_rank(vol_zscore_20, lookback)
    pr_oi_delta = percentile_rank(oi_delta_1h, lookback)
    pr_funding_imbalance = percentile_rank(funding_rate_col, lookback)
    pr_momentum = percentile_rank(momentum_col, lookback)

    # pr_cvd_divergence: percentile rank of |cvd_slope_10 - price_slope_10|
    price_slope_10 = _rolling_price_slope(df["close"], 10)
    cvd_div_abs = (cvd_slope_10 - price_slope_10).abs()
    pr_cvd_divergence = percentile_rank(cvd_div_abs, lookback)

    # ── Assemble output DataFrame ─────────────────────────────────────────────
    result = pd.DataFrame(
        {
            "open_time": df["open_time"],
            "atr_14": atr_14,
            "atr_pct": atr_pct,
            "rsi_14": rsi_14,
            "ema_20": ema_20,
            "ema_50": ema_50,
            "ema_200": ema_200,
            "macd": macd_res.macd,
            "macd_signal": macd_res.signal,
            "macd_hist": macd_res.hist,
            "obv": obv_series,
            "vol_zscore_20": vol_zscore_20.reset_index(drop=True),
            "oi_delta_pct_1h": oi_delta_1h,
            "oi_delta_pct_24h": oi_delta_24h,
            "funding_rate": funding_rate_col,
            "funding_z_30d": funding_z_col,
            "realized_vol_30d": rv30,
            "cvd_30": cvd_30,
            "cvd_slope_10": cvd_slope_10,
            # xvenue and basis filled with NaN; DB wrapper populates these
            "funding_divergence": float("nan"),
            "funding_divergence_z_30d": float("nan"),
            "funding_peer_count": float("nan"),
            "basis_premium": float("nan"),
            "basis_z_30d": float("nan"),
            "momentum_composite": momentum_col,
            "pr_atr": pr_atr.reset_index(drop=True),
            "pr_rsi": pr_rsi.reset_index(drop=True),
            "pr_vol_zscore": pr_vol_zscore.reset_index(drop=True),
            "pr_oi_delta": pr_oi_delta.reset_index(drop=True),
            "pr_funding_imbalance": pr_funding_imbalance.reset_index(drop=True),
            "pr_momentum": pr_momentum.reset_index(drop=True),
            "pr_cvd_divergence": pr_cvd_divergence.reset_index(drop=True),
        }
    )
    return result


def _rolling_price_slope(close: pd.Series, window: int) -> pd.Series:
    """Rolling OLS slope of close price over last `window` bars (closed='left')."""
    x = np.arange(window, dtype=float)
    slopes: list[float] = []
    arr = close.to_numpy(dtype=float)
    for i in range(len(arr)):
        start = i - window
        if start < 0:
            slopes.append(float("nan"))
            continue
        y = arr[start:i]
        if np.any(np.isnan(y)):
            slopes.append(float("nan"))
            continue
        slopes.append(float(np.polyfit(x, y, 1)[0]))
    return pd.Series(slopes, index=close.index, dtype=float)


async def upsert_features(
    session: AsyncSession,
    symbol: str,
    timeframe: str,
    df: pd.DataFrame,
) -> int:
    """Upsert feature rows. ON CONFLICT (symbol, timeframe, open_time) DO UPDATE SET ..."""
    if df.empty:
        return 0

    def _float_or_none(v: Any) -> float | None:
        if v is None:
            return None
        try:
            f = float(v)
            return None if np.isnan(f) else f
        except (TypeError, ValueError):
            return None

    def _int_or_none(v: Any) -> int | None:
        if v is None:
            return None
        try:
            f = float(v)
            return None if np.isnan(f) else int(f)
        except (TypeError, ValueError):
            return None

    values = []
    for _, row in df.iterrows():
        ot = row["open_time"]
        if isinstance(ot, pd.Timestamp):
            ot = ot.to_pydatetime()
        values.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "open_time": ot,
                "atr_14": _float_or_none(row.get("atr_14")),
                "atr_pct": _float_or_none(row.get("atr_pct")),
                "rsi_14": _float_or_none(row.get("rsi_14")),
                "ema_20": _float_or_none(row.get("ema_20")),
                "ema_50": _float_or_none(row.get("ema_50")),
                "ema_200": _float_or_none(row.get("ema_200")),
                "macd": _float_or_none(row.get("macd")),
                "macd_signal": _float_or_none(row.get("macd_signal")),
                "macd_hist": _float_or_none(row.get("macd_hist")),
                "obv": _float_or_none(row.get("obv")),
                "vol_zscore_20": _float_or_none(row.get("vol_zscore_20")),
                "oi_delta_pct_1h": _float_or_none(row.get("oi_delta_pct_1h")),
                "oi_delta_pct_24h": _float_or_none(row.get("oi_delta_pct_24h")),
                "funding_rate": _float_or_none(row.get("funding_rate")),
                "funding_z_30d": _float_or_none(row.get("funding_z_30d")),
                "realized_vol_30d": _float_or_none(row.get("realized_vol_30d")),
                "cvd_30": _float_or_none(row.get("cvd_30")),
                "cvd_slope_10": _float_or_none(row.get("cvd_slope_10")),
                "funding_divergence": _float_or_none(row.get("funding_divergence")),
                "funding_divergence_z_30d": _float_or_none(row.get("funding_divergence_z_30d")),
                "funding_peer_count": _int_or_none(row.get("funding_peer_count")),
                "basis_premium": _float_or_none(row.get("basis_premium")),
                "basis_z_30d": _float_or_none(row.get("basis_z_30d")),
                "momentum_composite": _float_or_none(row.get("momentum_composite")),
                "pr_atr": _float_or_none(row.get("pr_atr")),
                "pr_rsi": _float_or_none(row.get("pr_rsi")),
                "pr_vol_zscore": _float_or_none(row.get("pr_vol_zscore")),
                "pr_oi_delta": _float_or_none(row.get("pr_oi_delta")),
                "pr_funding_imbalance": _float_or_none(row.get("pr_funding_imbalance")),
                "pr_momentum": _float_or_none(row.get("pr_momentum")),
                "pr_cvd_divergence": _float_or_none(row.get("pr_cvd_divergence")),
                "feature_version": 1,
            }
        )

    await session.execute(
        text("""
            INSERT INTO features
              (symbol, timeframe, open_time,
               atr_14, atr_pct, rsi_14, ema_20, ema_50, ema_200,
               macd, macd_signal, macd_hist, obv,
               vol_zscore_20, oi_delta_pct_1h, oi_delta_pct_24h,
               funding_rate, funding_z_30d, realized_vol_30d,
               cvd_30, cvd_slope_10,
               funding_divergence, funding_divergence_z_30d, funding_peer_count,
               basis_premium, basis_z_30d,
               momentum_composite,
               pr_atr, pr_rsi, pr_vol_zscore, pr_oi_delta,
               pr_funding_imbalance, pr_momentum, pr_cvd_divergence,
               feature_version)
            VALUES
              (:symbol, :timeframe, :open_time,
               :atr_14, :atr_pct, :rsi_14, :ema_20, :ema_50, :ema_200,
               :macd, :macd_signal, :macd_hist, :obv,
               :vol_zscore_20, :oi_delta_pct_1h, :oi_delta_pct_24h,
               :funding_rate, :funding_z_30d, :realized_vol_30d,
               :cvd_30, :cvd_slope_10,
               :funding_divergence, :funding_divergence_z_30d, :funding_peer_count,
               :basis_premium, :basis_z_30d,
               :momentum_composite,
               :pr_atr, :pr_rsi, :pr_vol_zscore, :pr_oi_delta,
               :pr_funding_imbalance, :pr_momentum, :pr_cvd_divergence,
               :feature_version)
            ON CONFLICT (symbol, timeframe, open_time) DO UPDATE SET
              atr_14=EXCLUDED.atr_14, atr_pct=EXCLUDED.atr_pct,
              rsi_14=EXCLUDED.rsi_14,
              ema_20=EXCLUDED.ema_20, ema_50=EXCLUDED.ema_50, ema_200=EXCLUDED.ema_200,
              macd=EXCLUDED.macd, macd_signal=EXCLUDED.macd_signal,
              macd_hist=EXCLUDED.macd_hist, obv=EXCLUDED.obv,
              vol_zscore_20=EXCLUDED.vol_zscore_20,
              oi_delta_pct_1h=EXCLUDED.oi_delta_pct_1h,
              oi_delta_pct_24h=EXCLUDED.oi_delta_pct_24h,
              funding_rate=EXCLUDED.funding_rate, funding_z_30d=EXCLUDED.funding_z_30d,
              realized_vol_30d=EXCLUDED.realized_vol_30d,
              cvd_30=EXCLUDED.cvd_30, cvd_slope_10=EXCLUDED.cvd_slope_10,
              funding_divergence=EXCLUDED.funding_divergence,
              funding_divergence_z_30d=EXCLUDED.funding_divergence_z_30d,
              funding_peer_count=EXCLUDED.funding_peer_count,
              basis_premium=EXCLUDED.basis_premium, basis_z_30d=EXCLUDED.basis_z_30d,
              momentum_composite=EXCLUDED.momentum_composite,
              pr_atr=EXCLUDED.pr_atr, pr_rsi=EXCLUDED.pr_rsi,
              pr_vol_zscore=EXCLUDED.pr_vol_zscore, pr_oi_delta=EXCLUDED.pr_oi_delta,
              pr_funding_imbalance=EXCLUDED.pr_funding_imbalance,
              pr_momentum=EXCLUDED.pr_momentum,
              pr_cvd_divergence=EXCLUDED.pr_cvd_divergence,
              feature_version=EXCLUDED.feature_version
        """),
        values,
    )
    await session.commit()
    return len(values)


async def run_once(
    session: AsyncSession,
    symbols: list[str],
    timeframes: tuple[str, ...] = ("15m", "1h", "4h"),
) -> None:
    """Compute features for the most recent closed bar for each symbol/timeframe."""
    for symbol in symbols:
        for tf in timeframes:
            candles_df = await _fetch_candles(session, symbol, tf, bars=500)
            if candles_df.empty:
                continue
            features_df = compute_features_frame(candles_df, tf=tf)
            # Only upsert the last bar
            last = features_df.tail(1)
            await upsert_features(session, symbol, tf, last)


async def backfill(
    session: AsyncSession,
    since: datetime,
    symbols: list[str],
    timeframes: tuple[str, ...] = ("15m", "1h", "4h"),
) -> None:
    """Compute and upsert features for all bars since `since`, idempotent."""
    for symbol in symbols:
        for tf in timeframes:
            candles_df = await _fetch_candles(session, symbol, tf, since=since)
            if candles_df.empty:
                continue
            features_df = compute_features_frame(candles_df, tf=tf)
            # Only upsert bars >= since
            mask = pd.to_datetime(features_df["open_time"], utc=True) >= pd.Timestamp(since)
            await upsert_features(session, symbol, tf, features_df[mask])


async def _fetch_candles(
    session: AsyncSession,
    symbol: str,
    timeframe: str,
    bars: int | None = None,
    since: datetime | None = None,
) -> pd.DataFrame:
    """Fetch candles from DB into a DataFrame."""
    if since is not None:
        # Include a warm-up window before `since` for rolling indicators
        warmup = since - timedelta(days=90)
        result = await session.execute(
            text("""
                SELECT open_time, open, high, low, close, volume, taker_buy_vol
                FROM candles
                WHERE symbol = :symbol AND timeframe = :tf
                  AND open_time >= :warmup
                ORDER BY open_time
            """),
            {"symbol": symbol, "tf": timeframe, "warmup": warmup},
        )
    else:
        result = await session.execute(
            text("""
                SELECT open_time, open, high, low, close, volume, taker_buy_vol
                FROM candles
                WHERE symbol = :symbol AND timeframe = :tf
                ORDER BY open_time DESC
                LIMIT :bars
            """),
            {"symbol": symbol, "tf": timeframe, "bars": bars or 500},
        )
    rows = result.fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(
        rows,
        columns=["open_time", "open", "high", "low", "close", "volume", "taker_buy_vol"],
    )
    for col in ["open", "high", "low", "close", "volume", "taker_buy_vol"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if since is None:
        df = df.sort_values("open_time").reset_index(drop=True)
    return df
