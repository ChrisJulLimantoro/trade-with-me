from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ats.ingestion.xvenue_funding import _align_8h
from ats.logging import get_logger

log = get_logger(__name__)

BINANCE_FUTURES_BASE = "https://fapi.binance.com"
WEIGHT_LIMIT = 1500  # leave headroom below Binance's 2400/min


async def _get(
    client: httpx.AsyncClient, path: str, params: dict[str, Any]
) -> tuple[list[Any] | dict[str, Any], int]:
    """Return (data, used_weight). Sleeps briefly if approaching weight limit."""
    resp = await client.get(f"{BINANCE_FUTURES_BASE}{path}", params=params)
    resp.raise_for_status()
    used = int(resp.headers.get("X-MBX-USED-WEIGHT-1M", 0))
    if used > WEIGHT_LIMIT:
        log.warning("binance_weight_high", used=used, sleeping_ms=1000)
        await asyncio.sleep(1.0)
    return resp.json(), used


async def fetch_klines(
    client: httpx.AsyncClient,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[list[Any]]:
    """Page through /fapi/v1/klines and return all closed bars."""
    all_rows: list[list[Any]] = []
    cursor = start_ms
    while cursor < end_ms:
        data, _ = await _get(
            client,
            "/fapi/v1/klines",
            {
                "symbol": symbol, "interval": interval,
                "startTime": cursor, "endTime": end_ms, "limit": 1500,
            },
        )
        if not data:
            break
        all_rows.extend(data)  # type: ignore[arg-type]
        last_open_time = int(data[-1][0])  # type: ignore[index]
        if last_open_time <= cursor:
            break
        cursor = last_open_time + 1
    return all_rows


async def fetch_funding(
    client: httpx.AsyncClient,
    symbol: str,
    *,
    start_time_ms: int | None = None,
    end_time_ms: int | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Fetch Binance USDT-M funding history, optionally paginating a time window.

    Without ``start_time_ms``, returns the most recent ``limit`` rows (legacy behaviour).
    With ``start_time_ms``, pages forward until ``end_time_ms`` (default: now).
    """
    if start_time_ms is None:
        params: dict[str, Any] = {"symbol": symbol, "limit": limit}
        if end_time_ms is not None:
            params["endTime"] = end_time_ms
        data, _ = await _get(client, "/fapi/v1/fundingRate", params)
        return data  # type: ignore[return-value]

    end_ms = end_time_ms if end_time_ms is not None else _now_ms_local()
    cursor = start_time_ms
    all_rows: list[dict[str, Any]] = []
    while cursor <= end_ms:
        data, _ = await _get(
            client,
            "/fapi/v1/fundingRate",
            {"symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": limit},
        )
        batch = data if isinstance(data, list) else []
        if not batch:
            break
        all_rows.extend(batch)  # type: ignore[arg-type]
        last_t = int(batch[-1]["fundingTime"])  # type: ignore[index]
        if len(batch) < limit or last_t >= end_ms:
            break
        next_cursor = last_t + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor
    return all_rows


def _now_ms_local() -> int:
    return int(datetime.now(tz=UTC).timestamp() * 1000)


async def fetch_open_interest(client: httpx.AsyncClient, symbol: str) -> dict[str, Any]:
    data, _ = await _get(client, "/fapi/v1/openInterest", {"symbol": symbol})
    return data  # type: ignore[return-value]


async def fetch_premium_index(client: httpx.AsyncClient, symbol: str) -> dict[str, Any]:
    data, _ = await _get(client, "/fapi/v1/premiumIndex", {"symbol": symbol})
    return data  # type: ignore[return-value]


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


async def upsert_candles(
    session: AsyncSession, symbol: str, timeframe: str, rows: list[list[Any]]
) -> int:
    if not rows:
        return 0
    values = []
    for r in rows:
        taker_buy = Decimal(str(r[9]))
        vol = Decimal(str(r[5]))
        values.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "open_time": _ms_to_dt(int(r[0])),
                "open": Decimal(str(r[1])),
                "high": Decimal(str(r[2])),
                "low": Decimal(str(r[3])),
                "close": Decimal(str(r[4])),
                "volume": vol,
                "quote_volume": Decimal(str(r[7])),
                "taker_buy_vol": taker_buy,
                "trades": int(r[8]),
            }
        )
    await session.execute(
        text("""
            INSERT INTO candles
              (symbol, timeframe, open_time, open, high, low, close,
               volume, quote_volume, taker_buy_vol, trades)
            VALUES
              (:symbol, :timeframe, :open_time, :open, :high, :low, :close,
               :volume, :quote_volume, :taker_buy_vol, :trades)
            ON CONFLICT (symbol, timeframe, open_time) DO UPDATE SET
              open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
              close=EXCLUDED.close, volume=EXCLUDED.volume,
              quote_volume=EXCLUDED.quote_volume,
              taker_buy_vol=EXCLUDED.taker_buy_vol, trades=EXCLUDED.trades
        """),
        values,
    )
    await session.commit()
    return len(values)


async def upsert_funding(
    session: AsyncSession, symbol: str, rows: list[dict[str, Any]]
) -> int:
    if not rows:
        return 0
    values = []
    xvenue_values = []
    for r in rows:
        # Snap to 00/08/16 UTC so the xvenue pivot joins Binance with Bybit/OKX/HL
        # (raw Binance fundingTime often carries a few ms past the boundary).
        ft = _align_8h(_ms_to_dt(int(r["fundingTime"])))
        rate = Decimal(str(r["fundingRate"]))
        values.append({"symbol": symbol, "funding_time": ft, "rate": rate})
        xvenue_values.append(
            {"exchange": "binance", "symbol": symbol, "funding_time": ft, "rate": rate}
        )

    await session.execute(
        text("""
            INSERT INTO funding_rates (symbol, funding_time, rate)
            VALUES (:symbol, :funding_time, :rate)
            ON CONFLICT (symbol, funding_time) DO UPDATE SET rate=EXCLUDED.rate
        """),
        values,
    )
    await session.execute(
        text("""
            INSERT INTO funding_rates_xvenue (exchange, symbol, funding_time, rate)
            VALUES (:exchange, :symbol, :funding_time, :rate)
            ON CONFLICT (exchange, symbol, funding_time) DO UPDATE SET rate=EXCLUDED.rate
        """),
        xvenue_values,
    )
    await session.commit()
    return len(values)


async def upsert_open_interest(
    session: AsyncSession, symbol: str, row: dict[str, Any]
) -> None:
    ts = _ms_to_dt(int(row["time"]))
    oi = Decimal(str(row["openInterest"]))
    await session.execute(
        text("""
            INSERT INTO open_interest (symbol, ts, oi)
            VALUES (:symbol, :ts, :oi)
            ON CONFLICT (symbol, ts) DO UPDATE SET oi=EXCLUDED.oi
        """),
        {"symbol": symbol, "ts": ts, "oi": oi},
    )
    await session.commit()


async def upsert_basis(session: AsyncSession, symbol: str, row: dict[str, Any]) -> None:
    ts = _ms_to_dt(int(row["time"]))
    mark = Decimal(str(row["markPrice"]))
    index = Decimal(str(row["indexPrice"]))
    premium = (mark - index) / index
    await session.execute(
        text("""
            INSERT INTO basis (symbol, ts, mark_price, index_price, premium_index)
            VALUES (:symbol, :ts, :mark_price, :index_price, :premium_index)
            ON CONFLICT (symbol, ts) DO UPDATE SET
              mark_price=EXCLUDED.mark_price, index_price=EXCLUDED.index_price,
              premium_index=EXCLUDED.premium_index
        """),
        {
            "symbol": symbol, "ts": ts,
            "mark_price": mark, "index_price": index, "premium_index": premium,
        },
    )
    await session.commit()
