"""The single trade-close path + the open-trades query.

Every exit route (stop/target/expiry, hard invalidation, observe-exit) funnels through
``close_trade`` so a closed trade is recorded once and reflected on once. Centralising it
keeps the post-mortem hook in exactly one place.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ats.config import settings
from ats.db.models import PaperTrade
from ats.engine.quantities import _f, _trade_notional_margin
from ats.engine.reports import ClosedTradeInfo, TickReport
from ats.execution.executor import close_paper_trade
from ats.execution.reconcile import ExitResult
from ats.llm.client import Reflector
from ats.logging import get_logger

log = get_logger(__name__)


async def open_trades_for(
    session: AsyncSession, symbol: str, *, run_id: str | None = None
) -> list[PaperTrade]:
    stmt = select(PaperTrade).where(PaperTrade.symbol == symbol, PaperTrade.status == "open")
    if run_id is not None:
        stmt = stmt.where(PaperTrade.run_id == run_id)
    return list((await session.execute(stmt)).scalars().all())


async def close_trade(
    session: AsyncSession,
    client: Reflector | None,
    trade: PaperTrade,
    exit_result: ExitResult,
    report: TickReport,
    *,
    atr_at_close: float | None = None,
) -> None:
    """Single close path: record the close, then run the episodic post-mortem.

    Centralised so every exit route (stop/target/expiry, hard invalidation, observe-exit)
    reflects exactly once. Reflection failures never break the trade close.
    """
    closed_result = await close_paper_trade(
        session,
        trade.trade_id,
        exit_result,
        equity_usd=settings.risk.paper_equity_usd,
        size_pct=_f(trade.size_pct),
    )
    if closed_result is None:
        return
    notional_usd, margin_usd = _trade_notional_margin(trade)
    report.closed += 1
    report.closed_trades.append(
        ClosedTradeInfo(
            direction=trade.direction,
            entry_price=_f(trade.entry_price),
            stop_loss=_f(trade.stop_loss),
            exit_price=closed_result.exit_price,
            exit_reason=closed_result.exit_reason,
            margin_usd=margin_usd,
            notional_usd=notional_usd,
            leverage=_f(trade.leverage) if trade.leverage is not None else None,
            pnl_pct=closed_result.pnl_pct,
            pnl_usd=round(notional_usd * exit_result.pnl_pct, 2),
            atr_at_close=atr_at_close,
        )
    )
    if client is not None and settings.memory_enabled:
        try:
            from ats.learning.post_mortem import reflect_and_store

            await reflect_and_store(session, client, trade, closed_result)
        except Exception as exc:  # noqa: BLE001 — reflection must never break the close
            log.warning("reflect_failed", trade_id=str(trade.trade_id), error=str(exc))
