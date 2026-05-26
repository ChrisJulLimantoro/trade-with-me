from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ats.ingestion.freshness import upsert_heartbeat
from ats.logging import get_logger

log = get_logger(__name__)

BYBIT_BASE = "https://api.bybit.com"
OKX_BASE = "https://www.okx.com"
HYPERLIQUID_BASE = "https://api.hyperliquid.xyz"


def _align_8h(dt: datetime) -> datetime:
    """Snap a datetime down to the nearest 00/08/16 UTC boundary."""
    hours = (dt.hour // 8) * 8
    return dt.replace(hour=hours, minute=0, second=0, microsecond=0)


async def fetch_bybit_funding(client: httpx.AsyncClient, symbol: str) -> list[dict[str, Any]]:
    resp = await client.get(
        f"{BYBIT_BASE}/v5/market/funding/history",
        params={"category": "linear", "symbol": symbol, "limit": 200},
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    rows = []
    for item in data.get("result", {}).get("list", []):
        ft = datetime.fromtimestamp(int(item["fundingRateTimestamp"]) / 1000, tz=UTC)
        rows.append({"funding_time": _align_8h(ft), "rate": Decimal(str(item["fundingRate"]))})
    return rows


async def fetch_okx_funding(client: httpx.AsyncClient, inst_id: str) -> list[dict[str, Any]]:
    resp = await client.get(
        f"{OKX_BASE}/api/v5/public/funding-rate-history",
        params={"instId": inst_id, "limit": 100},
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    rows = []
    for item in data.get("data", []):
        ft = datetime.fromtimestamp(int(item["fundingTime"]) / 1000, tz=UTC)
        rows.append({"funding_time": _align_8h(ft), "rate": Decimal(str(item["fundingRate"]))})
    return rows


async def fetch_hyperliquid_funding(
    client: httpx.AsyncClient, coin: str
) -> list[dict[str, Any]]:
    resp = await client.post(
        f"{HYPERLIQUID_BASE}/info",
        content=json.dumps({"type": "fundingHistory", "coin": coin}),
        headers={"Content-Type": "application/json"},
        timeout=15.0,
    )
    resp.raise_for_status()
    items = resp.json()
    rows = []
    for item in items:
        ft = datetime.fromtimestamp(int(item["time"]) / 1000, tz=UTC)
        rows.append({"funding_time": _align_8h(ft), "rate": Decimal(str(item["fundingRate"]))})
    return rows


async def _upsert_xvenue(
    session: AsyncSession, exchange: str, symbol: str, rows: list[dict[str, Any]]
) -> int:
    if not rows:
        return 0
    values = [{"exchange": exchange, "symbol": symbol, **r} for r in rows]
    await session.execute(
        text("""
            INSERT INTO funding_rates_xvenue (exchange, symbol, funding_time, rate)
            VALUES (:exchange, :symbol, :funding_time, :rate)
            ON CONFLICT (exchange, symbol, funding_time) DO UPDATE SET rate=EXCLUDED.rate
        """),
        values,
    )
    await session.commit()
    return len(values)


async def pull_all(
    session: AsyncSession,
    symbols: list[str],
    mapping: dict[str, dict[str, str]],
) -> None:
    """Fetch and store cross-venue funding for all symbols. Non-fatal on per-venue errors."""
    async with httpx.AsyncClient() as client:
        for symbol in symbols:
            sym_map = mapping.get(symbol, {})

            for venue, _fetcher, mapped_sym in [
                ("bybit", fetch_bybit_funding, sym_map.get("bybit")),
                ("okx", fetch_okx_funding, sym_map.get("okx")),
                ("hyperliquid", fetch_hyperliquid_funding, sym_map.get("hyperliquid")),
            ]:
                hb_class = f"funding_{venue}"
                if mapped_sym is None:
                    log.warning("xvenue_no_mapping", venue=venue, symbol=symbol)
                    await upsert_heartbeat(
                        session, hb_class, "disabled", f"no mapping for {symbol}"
                    )
                    continue
                try:
                    if venue == "bybit":
                        rows = await fetch_bybit_funding(client, mapped_sym)
                    elif venue == "okx":
                        rows = await fetch_okx_funding(client, mapped_sym)
                    else:
                        rows = await fetch_hyperliquid_funding(client, mapped_sym)
                    await _upsert_xvenue(session, venue, symbol, rows)
                    await upsert_heartbeat(session, hb_class, "ok")
                    log.info("xvenue_pulled", venue=venue, symbol=symbol, rows=len(rows))
                except Exception as exc:
                    log.warning("xvenue_failed", venue=venue, symbol=symbol, error=str(exc))
                    await upsert_heartbeat(session, hb_class, "disabled", str(exc))
