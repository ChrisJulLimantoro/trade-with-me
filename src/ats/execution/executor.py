"""Paper executor — records simulated trades. NEVER places a live order.

This is the only component that writes ``paper_trades``. There is intentionally no
exchange client here: the system is paper-only by design.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ats import trace
from ats.db.models import PaperTrade
from ats.execution.reconcile import ExitResult, PartialFill, TradeState
from ats.logging import get_logger

log = get_logger(__name__)


async def open_paper_trade(
    session: AsyncSession,
    setup: dict[str, Any],
    *,
    entry_price: float,
    entry_time: datetime,
    size_pct: float,
    reasons: list[str],
) -> uuid.UUID:
    """Insert an open paper trade for a detected+confirmed+risk-approved setup.

    ``setup`` is a dict with setup_id, plan_id, symbol, direction, stop_loss, take_profit.
    """
    trade = PaperTrade(
        trade_id=uuid.uuid4(),
        setup_id=setup["setup_id"],
        plan_id=setup["plan_id"],
        symbol=setup["symbol"],
        direction=setup["direction"],
        size_pct=size_pct,
        entry_price=entry_price,
        entry_time=entry_time,
        stop_loss=float(setup["stop_loss"]),
        take_profit=[float(x) for x in setup["take_profit"]],
        status="open",
        reasons=reasons,
    )
    session.add(trade)
    await session.flush()
    log.info(
        "paper_trade_opened",
        trade_id=str(trade.trade_id),
        symbol=trade.symbol,
        direction=trade.direction,
        entry=entry_price,
        size_pct=size_pct,
    )
    trace.trade_opened(
        now=entry_time,
        trade_id=trade.trade_id,
        plan_id=trade.plan_id,
        setup_id=trade.setup_id,
        direction=trade.direction,
        entry=entry_price,
        size_pct=size_pct,
    )
    return trade.trade_id


def _merge_state(trade: PaperTrade, state: TradeState) -> None:
    """Persist updated exit state into ``trade_metadata`` (preserving other keys)."""
    md = dict(trade.trade_metadata or {})
    md.update(state.to_metadata())
    trade.trade_metadata = md


async def record_partial_exit(
    session: AsyncSession,
    trade_id: uuid.UUID,
    partial: PartialFill,
    state: TradeState,
    *,
    equity_usd: float,
    size_pct: float,
) -> None:
    """Bank a scaled-out leg: accrue realized pnl into ``trade_metadata``, stay open."""
    trade = await session.get(PaperTrade, trade_id)
    if trade is None:
        log.warning("paper_trade_missing_on_partial", trade_id=str(trade_id))
        return
    leg_usd = round(equity_usd * size_pct * partial.frac * partial.pnl_pct, 2)
    md = dict(trade.trade_metadata or {})
    md.update(state.to_metadata())
    legs = list(md.get("partials", []))
    legs.append(
        {
            "tp_index": partial.tp_index,
            "price": partial.price,
            "frac": round(partial.frac, 4),
            "pnl_pct": round(partial.pnl_pct, 6),
            "pnl_usd": leg_usd,
        }
    )
    md["partials"] = legs
    trade.trade_metadata = md
    await session.flush()
    log.info(
        "paper_trade_partial",
        trade_id=str(trade_id),
        tp_index=partial.tp_index,
        frac=round(partial.frac, 4),
        pnl_pct=round(partial.pnl_pct, 4),
    )
    trace.outcome(
        f"SCALED OUT {partial.frac:.2f} at tp{partial.tp_index + 1} "
        f"@ {partial.price} (leg {partial.pnl_pct * 100:+.2f}%) — stop→breakeven"
    )


async def update_trade_state(
    session: AsyncSession, trade_id: uuid.UUID, state: TradeState
) -> None:
    """Persist a no-fill state change (e.g. a trailing-stop move) for an open trade."""
    trade = await session.get(PaperTrade, trade_id)
    if trade is None:
        return
    _merge_state(trade, state)
    await session.flush()


async def close_paper_trade(
    session: AsyncSession,
    trade_id: uuid.UUID,
    exit_result: ExitResult,
    *,
    equity_usd: float,
    size_pct: float,
) -> None:
    """Mark a trade closed and record realized pnl."""
    trade = await session.get(PaperTrade, trade_id)
    if trade is None:
        log.warning("paper_trade_missing_on_close", trade_id=str(trade_id))
        return
    trade.exit_price = exit_result.exit_price
    trade.exit_time = exit_result.exit_time
    trade.exit_reason = exit_result.exit_reason
    trade.pnl_pct = exit_result.pnl_pct
    trade.pnl_usd = round(equity_usd * size_pct * exit_result.pnl_pct, 2)
    trade.status = "closed"
    await session.flush()
    log.info(
        "paper_trade_closed",
        trade_id=str(trade_id),
        reason=exit_result.exit_reason,
        pnl_pct=round(exit_result.pnl_pct, 4),
    )
    trace.trade_closed(
        now=exit_result.exit_time,
        trade_id=trade_id,
        plan_id=trade.plan_id,
        reason=exit_result.exit_reason,
        pnl_pct=exit_result.pnl_pct,
    )
