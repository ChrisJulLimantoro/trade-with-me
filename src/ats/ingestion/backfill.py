from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ats.config import settings
from ats.ingestion import binance_rest as br
from ats.ingestion.freshness import upsert_heartbeat
from ats.logging import get_logger

log = get_logger(__name__)


def _default_timeframes() -> tuple[str, ...]:
    """Decision timeframes plus the finer observe timeframe (for dynamic exit management)."""
    base = ("15m", "1h", "4h")
    tf = settings.observe_timeframe
    return (tf, *base) if tf and tf not in base else base


TIMEFRAMES = _default_timeframes()


def _now_ms() -> int:
    return int(datetime.now(tz=UTC).timestamp() * 1000)


def _since_ms(since: timedelta) -> int:
    return int((datetime.now(tz=UTC) - since).timestamp() * 1000)


async def run(
    session: AsyncSession,
    since: timedelta,
    symbols: list[str],
    timeframes: tuple[str, ...] = TIMEFRAMES,
) -> None:
    start_ms = _since_ms(since)
    end_ms = _now_ms()

    async with httpx.AsyncClient(timeout=30.0) as client:
        for symbol in symbols:
            for tf in timeframes:
                log.info("backfill_klines_start", symbol=symbol, timeframe=tf)
                rows = await br.fetch_klines(client, symbol, tf, start_ms, end_ms)
                count = await br.upsert_candles(session, symbol, tf, rows)
                log.info("backfill_klines_done", symbol=symbol, timeframe=tf, rows=count)
                await upsert_heartbeat(session, f"kline_{tf}", "ok")

            log.info("backfill_funding_start", symbol=symbol)
            funding_rows = await br.fetch_funding(client, symbol)
            count = await br.upsert_funding(session, symbol, funding_rows)
            log.info("backfill_funding_done", symbol=symbol, rows=count)
            await upsert_heartbeat(session, "funding", "ok")

            log.info("backfill_oi_start", symbol=symbol)
            oi_row = await br.fetch_open_interest(client, symbol)
            await br.upsert_open_interest(session, symbol, oi_row)
            log.info("backfill_oi_done", symbol=symbol)
            await upsert_heartbeat(session, "open_int", "ok")

            log.info("backfill_basis_start", symbol=symbol)
            basis_row = await br.fetch_premium_index(client, symbol)
            await br.upsert_basis(session, symbol, basis_row)
            log.info("backfill_basis_done", symbol=symbol)
            await upsert_heartbeat(session, "basis", "ok")

    # Cross-venue funding (Bybit/OKX/Hyperliquid) — historical, powers funding_divergence.
    # Non-fatal: a venue/mapping miss must not abort the Binance backfill above.
    try:
        from ats.ingestion.universe import load_xvenue_mapping
        from ats.ingestion.xvenue_funding import pull_all as pull_xvenue

        await pull_xvenue(session, symbols, load_xvenue_mapping())
        await upsert_heartbeat(session, "xvenue_funding", "ok")
        log.info("backfill_xvenue_done", symbols=symbols)
    except Exception as exc:  # noqa: BLE001 — advisory feed; never block the core backfill
        log.warning("backfill_xvenue_failed", error=str(exc))
