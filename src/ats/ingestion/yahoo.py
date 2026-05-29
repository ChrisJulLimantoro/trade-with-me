"""Yahoo Finance — exploratory feasibility probe (NOT wired into M1 ingestion).

`yfinance` scrapes Yahoo's public endpoints; no API key required. This module is a
thin read-only fetch used by `ats data yahoo` to evaluate whether Yahoo is a viable
source for macro regime context (e.g. ^GSPC, DX-Y.NYB, GC=F, ^TNX) or a crypto spot
cross-check (BTC-USD). No persistence/schema yet — that decision is deferred.
"""

from __future__ import annotations

import asyncio
from typing import Any


def _fetch_sync(ticker: str, period: str, interval: str) -> list[dict[str, Any]]:
    import yfinance  # imported lazily — optional dependency

    hist = yfinance.Ticker(ticker).history(period=period, interval=interval)
    rows: list[dict[str, Any]] = []
    for ts, row in hist.iterrows():
        rows.append(
            {
                "ts": ts.to_pydatetime(),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row["Volume"]),
            }
        )
    return rows


async def fetch_recent(
    ticker: str, period: str = "1mo", interval: str = "1d"
) -> list[dict[str, Any]]:
    """Fetch recent OHLCV bars for a Yahoo ticker. Runs the sync client off-thread."""
    return await asyncio.to_thread(_fetch_sync, ticker, period, interval)
