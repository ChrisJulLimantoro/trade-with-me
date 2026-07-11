from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncio
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ats.ingestion.freshness import upsert_heartbeat
from ats.logging import get_logger

log = get_logger(__name__)

BYBIT_BASE = "https://api.bybit.com"
OKX_BASE = "https://www.okx.com"
HYPERLIQUID_BASE = "https://api.hyperliquid.xyz"

# Default lookback when callers omit ``since`` (covers the ~90-sample 8h z-score window).
_DEFAULT_LOOKBACK_DAYS = 35
# HL returns at most 500 rows per request (hourly funding → ~20.8d per page).
_HL_PAGE_CAP = 500
_BYBIT_PAGE = 200
_OKX_PAGE = 100


def _align_8h(dt: datetime) -> datetime:
    """Snap a datetime down to the nearest 00/08/16 UTC boundary."""
    hours = (dt.hour // 8) * 8
    return dt.replace(hour=hours, minute=0, second=0, microsecond=0)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _default_start_ms() -> int:
    return _ms(datetime.now(tz=UTC) - timedelta(days=_DEFAULT_LOOKBACK_DAYS))


async def fetch_bybit_funding(
    client: httpx.AsyncClient,
    symbol: str,
    *,
    start_time_ms: int | None = None,
    end_time_ms: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch Bybit linear funding history.

    Paginates by walking ``endTime`` backwards (API returns ≤200 rows up to endTime).
    Without ``start_time_ms``, only the most recent page is fetched (legacy cadence pull).
    """
    if start_time_ms is None:
        params: dict[str, Any] = {"category": "linear", "symbol": symbol, "limit": _BYBIT_PAGE}
        if end_time_ms is not None:
            params["endTime"] = end_time_ms
        resp = await client.get(
            f"{BYBIT_BASE}/v5/market/funding/history", params=params, timeout=15.0
        )
        resp.raise_for_status()
        items = resp.json().get("result", {}).get("list", [])
        return _bybit_rows(items)

    start_ms = start_time_ms
    end_ms = end_time_ms if end_time_ms is not None else _ms(datetime.now(tz=UTC))
    collected: list[dict[str, Any]] = []
    cursor_end = end_ms
    while cursor_end > start_ms:
        resp = await client.get(
            f"{BYBIT_BASE}/v5/market/funding/history",
            params={
                "category": "linear",
                "symbol": symbol,
                "endTime": cursor_end,
                "limit": _BYBIT_PAGE,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        items = resp.json().get("result", {}).get("list", [])
        if not items:
            break
        collected.extend(items)
        oldest = min(int(i["fundingRateTimestamp"]) for i in items)
        if oldest <= start_ms:
            break
        next_end = oldest - 1
        if next_end >= cursor_end:
            break
        cursor_end = next_end

    # Keep rows in [start_ms, end_ms], collapse duplicate aligned stamps (last wins).
    by_boundary: dict[datetime, Decimal] = {}
    for item in collected:
        ts = int(item["fundingRateTimestamp"])
        if ts < start_ms or ts > end_ms:
            continue
        ft = _align_8h(datetime.fromtimestamp(ts / 1000, tz=UTC))
        by_boundary[ft] = Decimal(str(item["fundingRate"]))
    return [{"funding_time": t, "rate": r} for t, r in sorted(by_boundary.items())]


def _bybit_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        ft = datetime.fromtimestamp(int(item["fundingRateTimestamp"]) / 1000, tz=UTC)
        rows.append({"funding_time": _align_8h(ft), "rate": Decimal(str(item["fundingRate"]))})
    return rows


async def fetch_okx_funding(
    client: httpx.AsyncClient,
    inst_id: str,
    *,
    start_time_ms: int | None = None,
    end_time_ms: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch OKX swap funding history.

    OKX public history is short (~90d). ``after`` paginates toward older stamps.
    Without ``start_time_ms``, only the most recent page is fetched.
    """
    if start_time_ms is None:
        resp = await client.get(
            f"{OKX_BASE}/api/v5/public/funding-rate-history",
            params={"instId": inst_id, "limit": _OKX_PAGE},
            timeout=15.0,
        )
        resp.raise_for_status()
        items = resp.json().get("data", [])
        return _okx_rows(items)

    start_ms = start_time_ms
    end_ms = end_time_ms if end_time_ms is not None else _ms(datetime.now(tz=UTC))
    collected: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        params: dict[str, Any] = {"instId": inst_id, "limit": _OKX_PAGE}
        if after is not None:
            params["after"] = after
        resp = await client.get(
            f"{OKX_BASE}/api/v5/public/funding-rate-history",
            params=params,
            timeout=15.0,
        )
        resp.raise_for_status()
        items = resp.json().get("data", [])
        if not items:
            break
        collected.extend(items)
        oldest = min(int(i["fundingTime"]) for i in items)
        if oldest <= start_ms:
            break
        next_after = str(oldest)
        if next_after == after:
            break
        after = next_after

    by_boundary: dict[datetime, Decimal] = {}
    for item in collected:
        ts = int(item["fundingTime"])
        if ts < start_ms or ts > end_ms:
            continue
        ft = _align_8h(datetime.fromtimestamp(ts / 1000, tz=UTC))
        by_boundary[ft] = Decimal(str(item["fundingRate"]))
    return [{"funding_time": t, "rate": r} for t, r in sorted(by_boundary.items())]


def _okx_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        ft = datetime.fromtimestamp(int(item["fundingTime"]) / 1000, tz=UTC)
        rows.append({"funding_time": _align_8h(ft), "rate": Decimal(str(item["fundingRate"]))})
    return rows


async def fetch_hyperliquid_funding(
    client: httpx.AsyncClient,
    coin: str,
    *,
    start_time_ms: int | None = None,
    end_time_ms: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch HL funding history.

    ``startTime`` is required by the API (omitting it returns HTTP 422). HL publishes
    hourly rates (cap 500/page); we paginate and snap each stamp to the 8h UTC boundary.
    Multiple hours in one bucket collapse via last-wins on upsert (chronological order).
    """
    now_ms = _ms(datetime.now(tz=UTC))
    cursor = start_time_ms if start_time_ms is not None else _default_start_ms()
    end_ms = end_time_ms if end_time_ms is not None else now_ms

    raw: list[dict[str, Any]] = []
    while cursor <= end_ms:
        body: dict[str, Any] = {
            "type": "fundingHistory",
            "coin": coin,
            "startTime": cursor,
        }
        if end_time_ms is not None:
            body["endTime"] = end_time_ms
        items: list[dict[str, Any]] | None = None
        last_resp: httpx.Response | None = None
        for attempt in range(6):
            last_resp = await client.post(
                f"{HYPERLIQUID_BASE}/info",
                content=json.dumps(body),
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )
            if last_resp.status_code == 429:
                await asyncio.sleep(2 ** attempt)
                continue
            last_resp.raise_for_status()
            items = last_resp.json()
            break
        if items is None:
            if last_resp is not None:
                last_resp.raise_for_status()
            raise RuntimeError(f"hyperliquid fundingHistory empty retries for {coin}")
        if not items:
            break
        raw.extend(items)
        last_t = int(items[-1]["time"])
        if len(items) < _HL_PAGE_CAP or last_t >= end_ms:
            break
        next_cursor = last_t + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        # Pace deep backfills so HL doesn't 429 mid-pagination.
        await asyncio.sleep(0.2)

    # Collapse hourly → 8h: later stamps in a bucket overwrite earlier ones.
    by_boundary: dict[datetime, Decimal] = {}
    for item in raw:
        ft = datetime.fromtimestamp(int(item["time"]) / 1000, tz=UTC)
        by_boundary[_align_8h(ft)] = Decimal(str(item["fundingRate"]))
    return [
        {"funding_time": t, "rate": rate}
        for t, rate in sorted(by_boundary.items())
    ]


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
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> None:
    """Fetch and store cross-venue funding for all symbols. Non-fatal on per-venue errors.

    ``since``/``until`` select a historical window (inclusive). When omitted, each venue
    uses its short recent-page pull (live cadence). Pass ``since`` for deep backfills
    (e.g. 2025-01-01) so Bybit/OKX/HL paginate.
    """
    start_ms = _ms(since) if since is not None else None
    end_ms = _ms(until) if until is not None else None
    hist_kwargs: dict[str, int] = {}
    if start_ms is not None:
        hist_kwargs["start_time_ms"] = start_ms
    if end_ms is not None:
        hist_kwargs["end_time_ms"] = end_ms

    async with httpx.AsyncClient() as client:
        for symbol in symbols:
            sym_map = mapping.get(symbol, {})

            for venue, mapped_sym in [
                ("bybit", sym_map.get("bybit")),
                ("okx", sym_map.get("okx")),
                ("hyperliquid", sym_map.get("hyperliquid")),
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
                        rows = await fetch_bybit_funding(client, mapped_sym, **hist_kwargs)
                    elif venue == "okx":
                        rows = await fetch_okx_funding(client, mapped_sym, **hist_kwargs)
                    else:
                        # HL always needs startTime; default lookback when since omitted.
                        rows = await fetch_hyperliquid_funding(
                            client, mapped_sym, **hist_kwargs
                        )
                    await _upsert_xvenue(session, venue, symbol, rows)
                    await upsert_heartbeat(session, hb_class, "ok")
                    log.info("xvenue_pulled", venue=venue, symbol=symbol, rows=len(rows))
                    if start_ms is not None and venue == "hyperliquid":
                        await asyncio.sleep(1.0)
                except Exception as exc:
                    log.warning("xvenue_failed", venue=venue, symbol=symbol, error=str(exc))
                    await upsert_heartbeat(session, hb_class, "disabled", str(exc))
