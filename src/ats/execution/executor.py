"""Paper executor — records simulated trades. NEVER places a live order.

This is the only component that writes ``paper_trades``. There is intentionally no
exchange client here: the system is paper-only by design.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ats.db.models import PaperTrade
from ats.execution.reconcile import ExitResult
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
    return trade.trade_id


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
