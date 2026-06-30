"""Websocket-driven live loop (event-driven, bar-close-precise).

A quick, robust integration: the kline websocket is used purely as a precise *trigger*. On a
CLOSED bar event we reuse the same REST ``refresh_fn`` the poll loop uses (backfill + feature
recompute) to populate the DB, then run one tick. This fires decisions the instant a bar closes
(no fixed-interval lag) without duplicating the candle-upsert schema. The user-data stream
records real fills into the order log so internal state reflects actual exchange behavior.

Falls back to the REST poll loop (``run_live``) if the socket can't be established.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from binance import BinanceSocketManager
from sqlalchemy.ext.asyncio import AsyncSession

from ats.engine.loop import run_tick
from ats.execution.live import LiveContext
from ats.execution.live_tracker import ReconcileMode, reconcile_on_start, snapshot
from ats.llm.client import LlmClient
from ats.logging import get_logger

log = get_logger(__name__)

_RECONNECT_BACKOFF = [1, 2, 5, 10, 30]  # seconds, then capped at the last value


async def run_ws(
    session_factory: Callable[[], AsyncSession],
    client: LlmClient,
    ctx: LiveContext,
    *,
    symbol: str,
    tf: str,
    observe_tf: str,
    refresh_fn: Callable[[], Awaitable[None]],
    reconcile_mode: ReconcileMode = "close",
    once: bool = False,
) -> None:
    """Run the event-driven websocket live loop until cancelled."""
    bsm = BinanceSocketManager(ctx.client.raw)
    streams = [f"{symbol.lower()}@kline_{tf}"]
    if observe_tf and observe_tf != tf:
        streams.append(f"{symbol.lower()}@kline_{observe_tf}")

    attempt = 0
    while True:
        try:
            # Reconcile on (re)connect so we never resume blind after a drop.
            async with session_factory() as session:
                await reconcile_on_start(session, ctx, symbol, mode=reconcile_mode)
                await session.commit()

            user_task = asyncio.create_task(_consume_user(bsm, ctx))
            try:
                await _consume_klines(
                    bsm, streams, session_factory, client, ctx,
                    symbol=symbol, tf=tf, refresh_fn=refresh_fn, once=once,
                )
            finally:
                user_task.cancel()
            return  # only reached when once=True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # connection dropped / transient — reconnect with backoff
            delay = _RECONNECT_BACKOFF[min(attempt, len(_RECONNECT_BACKOFF) - 1)]
            attempt += 1
            log.warning("live_ws_reconnect", symbol=symbol, error=str(exc), retry_in_s=delay)
            ctx.record("ws_reconnect", {"symbol": symbol, "error": str(exc), "retry_in_s": delay})
            await asyncio.sleep(delay)


async def _consume_klines(
    bsm: BinanceSocketManager,
    streams: list[str],
    session_factory: Callable[[], AsyncSession],
    client: LlmClient,
    ctx: LiveContext,
    *,
    symbol: str,
    tf: str,
    refresh_fn: Callable[[], Awaitable[None]],
    once: bool,
) -> None:
    socket = bsm.futures_multiplex_socket(streams)
    async with socket as stream:
        log.info("live_ws_connected", symbol=symbol, streams=streams)
        while True:
            msg = await stream.recv()
            data = msg.get("data", msg)
            kline = data.get("k")
            if not kline or not kline.get("x"):
                continue  # only act on CLOSED bars
            interval = kline.get("i")
            log.info("live_ws_bar_close", symbol=symbol, interval=interval)
            # Trigger: repopulate DB via REST, then run the decision/exit tick.
            await refresh_fn()
            async with session_factory() as session:
                tick = await run_tick(session, client, symbol=symbol, tf=tf)
            await snapshot_in_session(ctx, symbol)
            log.info(
                "live_ws_tick",
                symbol=symbol,
                interval=interval,
                detections=tick.detections,
                opened=tick.opened,
                closed=tick.closed,
            )
            # Only the DECISION timeframe's close advances on the once-shot path.
            if once and interval == tf:
                return


async def snapshot_in_session(ctx: LiveContext, symbol: str) -> None:
    try:
        await snapshot(ctx, symbol)
    except Exception as exc:  # snapshot is observability only — never break the loop
        log.warning("live_ws_snapshot_failed", symbol=symbol, error=str(exc))


async def _consume_user(bsm: BinanceSocketManager, ctx: LiveContext) -> None:
    """Record real order/account updates from the user-data stream into the order log."""
    try:
        async with bsm.futures_user_socket() as stream:
            while True:
                msg = await stream.recv()
                event = msg.get("e")
                if event == "ORDER_TRADE_UPDATE":
                    o = msg.get("o", {})
                    ctx.record(
                        "fill",
                        {
                            "symbol": o.get("s"),
                            "side": o.get("S"),
                            "order_id": str(o.get("i", "")),
                            "status": o.get("X"),
                            "last_filled_qty": o.get("l"),
                            "last_filled_price": o.get("L"),
                            "realized_pnl": o.get("rp"),
                        },
                    )
                elif event == "ACCOUNT_UPDATE":
                    ctx.record("account_update", {"reason": msg.get("a", {}).get("m")})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("live_ws_user_stream_failed", error=str(exc))
