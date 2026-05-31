"""Cross-venue funding divergence — pure core + DB wrapper."""

from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def funding_divergence_at(
    binance_rate: float | None,
    peer_rates: list[float | None],
) -> tuple[float | None, int]:
    """Pure core: compute divergence and peer count.

    peer_rates should contain rates from non-binance venues (bybit/okx/hyperliquid).
    peer_count = count of non-null peers.
    If peer_count < 2 → return (None, peer_count).
    divergence = binance_rate - median(non-null peers).
    """
    non_null = [r for r in peer_rates if r is not None]
    peer_count = len(non_null)
    if peer_count < 2 or binance_rate is None:
        return None, peer_count
    div = binance_rate - median(non_null)
    return div, peer_count


def funding_divergence_z(divergence_series: pd.Series, lookback: int = 90) -> pd.Series:
    """Rolling 30d z-score of funding divergence over 8h boundaries (~90 samples).

    Uses closed='left' to avoid look-ahead.
    """
    mean = divergence_series.rolling(lookback, closed="left").mean()
    std = divergence_series.rolling(lookback, closed="left").std()
    z = (divergence_series - mean) / std
    # Where divergence is NaN, z should also be NaN
    z = z.where(divergence_series.notna(), other=float("nan"))
    return z


async def get_divergence_for_bar(
    session: AsyncSession,
    symbol: str,
    open_time: datetime,
) -> tuple[float | None, float | None, int]:
    """DB wrapper: fetch rates at last 8h boundary ≤ open_time, compute divergence.

    Returns (divergence, divergence_z, peer_count).
    divergence_z is computed over the trailing 30d of 8h boundaries (~90 samples).
    """
    from sqlalchemy import text

    from ats.ingestion.xvenue_funding import _align_8h

    boundary = _align_8h(open_time)

    # Fetch the rate at this boundary for each exchange
    result = await session.execute(
        text("""
            SELECT exchange, rate
            FROM funding_rates_xvenue
            WHERE symbol = :symbol
              AND funding_time = :boundary
        """),
        {"symbol": symbol, "boundary": boundary},
    )
    rows = result.fetchall()
    rates: dict[str, float] = {r.exchange: float(r.rate) for r in rows}

    binance_rate = rates.get("binance")
    peer_rates: list[float | None] = [
        rates.get("bybit"),
        rates.get("okx"),
        rates.get("hyperliquid"),
    ]
    div, peer_count = funding_divergence_at(binance_rate, peer_rates)

    if div is None:
        return None, None, peer_count

    # Compute z-score over trailing 30d of 8h boundaries
    result2 = await session.execute(
        text("""
            SELECT funding_time,
                   MAX(CASE WHEN exchange='binance' THEN rate END) AS binance_rate,
                   MAX(CASE WHEN exchange='bybit'   THEN rate END) AS bybit_rate,
                   MAX(CASE WHEN exchange='okx'     THEN rate END) AS okx_rate,
                   MAX(CASE WHEN exchange='hyperliquid' THEN rate END) AS hl_rate
            FROM funding_rates_xvenue
            WHERE symbol = :symbol
              AND funding_time <= :boundary
              AND funding_time >= :boundary - INTERVAL '30 days'
            GROUP BY funding_time
            ORDER BY funding_time
        """),
        {"symbol": symbol, "boundary": boundary},
    )
    hist_rows = result2.fetchall()

    hist_divs: list[float | None] = []
    for hr in hist_rows:
        br = float(hr.binance_rate) if hr.binance_rate is not None else None
        peers: list[float | None] = [
            float(hr.bybit_rate) if hr.bybit_rate is not None else None,
            float(hr.okx_rate) if hr.okx_rate is not None else None,
            float(hr.hl_rate) if hr.hl_rate is not None else None,
        ]
        d, _ = funding_divergence_at(br, peers)
        hist_divs.append(d)

    valid = [d for d in hist_divs if d is not None]
    if len(valid) < 2:
        return div, None, peer_count

    arr = np.array(valid, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std())
    if std == 0:
        return div, None, peer_count
    z = (div - mean) / std
    return div, z, peer_count
