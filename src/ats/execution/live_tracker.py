"""Live position/order tracker — keeps internal state honest against the exchange.

Two jobs:
  1. ``reconcile_on_start`` — run ONCE before the first live tick (and on every websocket
     reconnect). Compares open testnet ``paper_trades`` against the real exchange position so
     the bot never starts blind. Because the mirror model holds NO resting protective orders,
     an orphan exchange position (no matching internal trade) is dangerous; default action is
     to flatten it so the run starts clean.
  2. ``snapshot`` — a per-tick one-liner (qty / entry / uPnL) logged so the file trace shows
     any divergence between the simulated book and the real testnet position.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ats.engine.closing import open_trades_for
from ats.execution.live import LiveContext, _side_for_reduce
from ats.logging import get_logger

log = get_logger(__name__)

ReconcileMode = Literal["close", "adopt", "warn"]


async def reconcile_on_start(
    session: AsyncSession,
    ctx: LiveContext,
    symbol: str,
    *,
    mode: ReconcileMode = "close",
) -> None:
    """Reconcile internal open trades vs the live testnet position for ``symbol``."""
    client = ctx.client
    await client.load_filters(symbol)
    pos = await client.position_risk(symbol)
    internal = await open_trades_for(session, symbol)
    internal_live = [t for t in internal if t.venue == "binance_testnet"]
    pos_amt = abs(float(pos.get("positionAmt", 0))) if pos else 0.0

    ctx.record(
        "reconcile",
        {
            "symbol": symbol,
            "mode": mode,
            "exchange_position_amt": pos_amt,
            "internal_open": len(internal_live),
        },
    )

    # Case A: exchange holds a position with no matching internal trade → orphan.
    if pos is not None and not internal_live:
        direction = "long" if float(pos["positionAmt"]) > 0 else "short"
        log.warning("live_orphan_position", symbol=symbol, amt=pos_amt, mode=mode)
        if mode == "close":
            qty = client.round_qty(symbol, pos_amt)
            if qty > 0:
                await client.market_order(
                    symbol, _side_for_reduce(direction), qty, reduce_only=True
                )
            ctx.record("reconcile", {"symbol": symbol, "action": "closed_orphan", "qty": qty})
        elif mode == "warn":
            ctx.record("reconcile", {"symbol": symbol, "action": "warn_orphan_left_open"})
        # "adopt": leave the position; the operator accepts it (no internal row created here —
        # the mirror model will manage the NEXT entry, this one rides unmanaged by design).

    # Case B: internal open trade(s) but the exchange is flat → drifted (manual/liq close).
    if pos is None and internal_live:
        for t in internal_live:
            log.warning("live_internal_without_position", symbol=symbol, trade_id=str(t.trade_id))
            ctx.record(
                "reconcile",
                {
                    "symbol": symbol,
                    "trade_id": str(t.trade_id),
                    "action": "internal_open_but_exchange_flat",
                },
            )


async def snapshot(ctx: LiveContext, symbol: str) -> None:
    """Log a one-line live position snapshot (qty / entry / unrealized PnL)."""
    pos = await ctx.client.position_risk(symbol)
    if pos is None:
        ctx.record("snapshot", {"symbol": symbol, "flat": True})
        return
    ctx.record(
        "snapshot",
        {
            "ts": datetime.now(UTC).isoformat(),
            "symbol": symbol,
            "position_amt": float(pos.get("positionAmt", 0)),
            "entry_price": float(pos.get("entryPrice", 0)),
            "unrealized_pnl": float(pos.get("unRealizedProfit", 0)),
        },
    )
