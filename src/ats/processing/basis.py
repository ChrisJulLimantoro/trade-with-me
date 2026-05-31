"""Perp basis premium — causal lookup + z-score."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def basis_premium_from_rows(
    basis_rows: list[tuple[datetime, float]],
    ts: datetime,
) -> float | None:
    """Pure core: most recent premium_index with row_ts <= ts.

    basis_rows: list of (ts, premium_index) sorted by ts ascending.
    Returns None if no row satisfies ts <= open_time.
    """
    result: float | None = None
    for row_ts, premium in basis_rows:
        if row_ts <= ts:
            result = premium
        else:
            break
    return result


def basis_z_from_series(
    premium_series: pd.Series,
    lookback: int = 8640,
) -> pd.Series:
    """Rolling 30d z-score of basis premium.

    lookback default: 30d × 288 bars/day @ 5m cadence = 8640.
    Uses closed='left' to avoid look-ahead.
    """
    mean = premium_series.rolling(lookback, closed="left").mean()
    std = premium_series.rolling(lookback, closed="left").std()
    z = (premium_series - mean) / std
    z = z.where(premium_series.notna(), other=float("nan"))
    return z


async def get_basis_for_bar(
    session: AsyncSession,
    symbol: str,
    open_time: datetime,
) -> tuple[float | None, float | None]:
    """DB wrapper: fetch most recent basis_premium ≤ open_time and its z-score.

    Returns (basis_premium, basis_z_30d).
    """
    from sqlalchemy import text

    # Most recent basis row with ts <= open_time
    result = await session.execute(
        text("""
            SELECT premium_index
            FROM basis
            WHERE symbol = :symbol
              AND ts <= :open_time
            ORDER BY ts DESC
            LIMIT 1
        """),
        {"symbol": symbol, "open_time": open_time},
    )
    row = result.fetchone()
    if row is None:
        return None, None

    premium = float(row.premium_index)

    # Compute z-score over trailing 30d
    result2 = await session.execute(
        text("""
            SELECT premium_index
            FROM basis
            WHERE symbol = :symbol
              AND ts <= :open_time
              AND ts >= :open_time - INTERVAL '30 days'
            ORDER BY ts
        """),
        {"symbol": symbol, "open_time": open_time},
    )
    hist_rows = result2.fetchall()
    arr = np.array([float(r.premium_index) for r in hist_rows], dtype=float)
    if len(arr) < 2:
        return premium, None
    mean = float(arr.mean())
    std = float(arr.std())
    if std == 0:
        return premium, None
    z = (premium - mean) / std
    return premium, z
