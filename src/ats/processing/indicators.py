"""Pure indicator functions — DataFrame in, Series (or dataclass) out. No DB."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pandas_ta as ta


@dataclass
class MACDResult:
    macd: pd.Series
    signal: pd.Series
    hist: pd.Series


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Average True Range (pandas-ta)."""
    result = ta.atr(df["high"], df["low"], df["close"], length=n)
    if result is None:
        return pd.Series(dtype=float, name=f"ATRr_{n}")
    return result


def rsi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Relative Strength Index (pandas-ta)."""
    result = ta.rsi(df["close"], length=n)
    if result is None:
        return pd.Series(dtype=float, name=f"RSI_{n}")
    return result


def macd(df: pd.DataFrame) -> MACDResult:
    """MACD (12/26/9). Returns MACDResult with .macd, .signal, .hist Series."""
    result = ta.macd(df["close"])
    n = len(df)
    empty = pd.Series([float("nan")] * n, dtype=float)
    if result is None or result.empty:
        return MACDResult(macd=empty.copy(), signal=empty.copy(), hist=empty.copy())
    cols = result.columns.tolist()
    # pandas-ta 0.4.x: MACD_12_26_9, MACDh_12_26_9, MACDs_12_26_9
    macd_col = next((c for c in cols if c.startswith("MACD_")), cols[0])
    hist_col = next((c for c in cols if "h_" in c), cols[1])
    sig_col = next((c for c in cols if c.startswith("MACDs")), cols[2])
    return MACDResult(
        macd=result[macd_col].reset_index(drop=True),
        signal=result[sig_col].reset_index(drop=True),
        hist=result[hist_col].reset_index(drop=True),
    )


def ema(df: pd.DataFrame, n: int) -> pd.Series:
    """Exponential Moving Average."""
    result = ta.ema(df["close"], length=n)
    if result is None:
        return pd.Series(dtype=float, name=f"EMA_{n}")
    return result


def obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume."""
    result = ta.obv(df["close"], df["volume"])
    if result is None:
        return pd.Series(dtype=float, name="OBV")
    return result


def realized_vol(df: pd.DataFrame, n: int = 30) -> pd.Series:
    """Annualized realized volatility: rolling std of log returns * sqrt(252*bars_per_day).

    Uses closed='left' so the current bar is excluded from its own window.
    """
    log_ret = np.log(df["close"] / df["close"].shift(1))
    return log_ret.rolling(n, closed="left").std() * np.sqrt(252 * 24)


def roc(df: pd.DataFrame, n: int = 5) -> pd.Series:
    """Rate of Change (pandas-ta)."""
    result = ta.roc(df["close"], length=n)
    if result is None:
        return pd.Series(dtype=float, name=f"ROC_{n}")
    return result


def cvd(df: pd.DataFrame, n: int = 30) -> tuple[pd.Series, pd.Series]:
    """Cumulative Volume Delta and 10-bar OLS slope.

    CVD formula: cumsum(2 * taker_buy_vol - volume).
    The 'n' governs the NULL rule only: if any taker_buy_vol in the trailing
    n-bar window is NULL, set cvd_30 and cvd_slope_10 to NaN for that bar.

    Returns (cvd_series, cvd_slope_10_series).
    """
    tbv = df["taker_buy_vol"]
    vol = df["volume"]

    # Identify bars where any of the trailing n bars has a null tbv
    has_null_in_window = tbv.isna().rolling(n, closed="left", min_periods=1).max().astype(bool)
    # Also null if tbv itself is null
    has_null_in_window = has_null_in_window | tbv.isna()

    delta = 2.0 * tbv - vol
    cvd_raw = delta.cumsum()
    cvd_series = cvd_raw.where(~has_null_in_window, other=float("nan"))
    cvd_series.name = f"cvd_{n}"

    # cvd_slope_10: OLS slope of cvd over last 10 bars (closed='left')
    slope_window = 10
    cvd_slope = _rolling_ols_slope(cvd_series, slope_window)
    cvd_slope.name = "cvd_slope_10"

    return cvd_series, cvd_slope


def _rolling_ols_slope(series: pd.Series, window: int) -> pd.Series:
    """Rolling OLS slope using numpy.polyfit over `window` bars (closed='left')."""
    x = np.arange(window, dtype=float)
    slopes = []
    arr = series.to_numpy(dtype=float)
    for i in range(len(arr)):
        # closed='left': use bars [i-window, i), i.e. NOT including bar i
        start = i - window
        if start < 0:
            slopes.append(float("nan"))
            continue
        y = arr[start:i]
        if np.any(np.isnan(y)):
            slopes.append(float("nan"))
            continue
        slope = float(np.polyfit(x, y, 1)[0])
        slopes.append(slope)
    return pd.Series(slopes, index=series.index, dtype=float)
