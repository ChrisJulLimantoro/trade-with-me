"""Paper executor — records simulated trades. NEVER places a live order.

This is the only component that writes ``paper_trades``. There is intentionally no
exchange client here: the system is paper-only by design.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ats import trace
from ats.db.models import PaperTrade
from ats.execution.live import live_ctx
from ats.execution.reconcile import ExitResult, PartialFill, TradeState
from ats.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class RunTag:
    """Identifies the replay run that opened a trade (for the A/B harness)."""

    run_id: str
    run_label: str | None = None
    config_hash: str | None = None


# Set by the replay loop around its bar walk; unset (None) for the live/tick path, so live
# trades persist with NULL run columns. ContextVar keeps the shared executor/detector
# signatures untouched.
current_run: ContextVar[RunTag | None] = ContextVar("current_run", default=None)


def margin_close_values(
    *, notional_usd: float, margin_usd: float, notional_pnl_pct: float
) -> tuple[float, float]:
    """Return ``(pnl_usd, margin_pnl_pct)`` from an internal notional-return result."""
    pnl_usd = round(notional_usd * notional_pnl_pct, 2)
    margin_pnl_pct = pnl_usd / margin_usd if margin_usd > 0 else 0.0
    return pnl_usd, margin_pnl_pct


async def open_paper_trade(
    session: AsyncSession,
    setup: dict[str, Any],
    *,
    entry_price: float,
    entry_time: datetime,
    size_pct: float,
    reasons: list[str],
    leverage: float | None = None,
    margin_usd: float | None = None,
    notional_usd: float | None = None,
    liq_price: float | None = None,
    risk_usd: float | None = None,
) -> uuid.UUID | None:
    """Insert an open paper trade for a detected+confirmed+risk-approved setup.

    ``setup`` is a dict with setup_id, plan_id, symbol, direction, stop_loss, take_profit.
    ``size_pct`` is notional / equity; ``margin_usd`` is committed capital before
    leverage, and ``notional_usd`` is market exposure after leverage.

    Returns the new trade_id, or ``None`` when live execution is on and the entry order was
    rejected/too small — the caller must NOT open an internal trade for a failed live entry.
    """
    run = current_run.get()
    # Mirror model: when live, place the real testnet entry order FIRST; abort (no internal
    # trade) if it didn't fill. The simulated ``entry_price`` stays the analytics source of
    # truth; the real fill is recorded in the order log via the client recorder.
    venue = "paper"
    entry_order_id: str | None = None
    ctx = live_ctx.get()
    if ctx is not None:
        if not notional_usd or notional_usd <= 0:
            log.warning("live_open_no_notional", symbol=setup["symbol"])
            trace.outcome("SKIPPED: live entry has no notional to size an order")
            return None
        result = await ctx.open_position(
            setup["symbol"],
            setup["direction"],
            notional_usd=float(notional_usd),
            entry_price=entry_price,
            leverage=leverage,
        )
        if result is None:
            trace.outcome("SKIPPED: live testnet entry order rejected/too small")
            return None
        venue = "binance_testnet"
        entry_order_id = result.order_id
    trade = PaperTrade(
        trade_id=uuid.uuid4(),
        setup_id=setup["setup_id"],
        plan_id=setup["plan_id"],
        symbol=setup["symbol"],
        direction=setup["direction"],
        size_pct=size_pct,
        leverage=leverage,
        margin_usd=margin_usd,
        notional_usd=notional_usd,
        liq_price=liq_price,
        risk_usd=risk_usd,
        entry_price=entry_price,
        entry_time=entry_time,
        stop_loss=float(setup["stop_loss"]),
        take_profit=[float(x) for x in setup["take_profit"]],
        status="open",
        reasons=reasons,
        run_id=run.run_id if run else None,
        run_label=run.run_label if run else None,
        config_hash=run.config_hash if run else None,
        venue=venue,
        entry_order_id=entry_order_id,
    )
    session.add(trade)
    await session.flush()
    # Engine-synced protection: drop a STOP_MARKET (SL) + TAKE_PROFIT_MARKET (TP) on the
    # exchange so the position is never unprotected. The exit machine keeps managing it and
    # will cancel/replace these as the stop moves / trade closes.
    if ctx is not None and venue == "binance_testnet":
        tps = setup.get("take_profit") or []
        if tps:
            await ctx.place_protection(
                str(trade.trade_id),
                setup["symbol"],
                setup["direction"],
                stop_loss=float(setup["stop_loss"]),
                take_profit=float(tps[-1]),
            )
    log.info(
        "paper_trade_opened",
        trade_id=str(trade.trade_id),
        symbol=trade.symbol,
        direction=trade.direction,
        entry=entry_price,
        size_pct=size_pct,
        margin_usd=margin_usd,
        notional_usd=notional_usd,
    )
    trace.trade_opened(
        now=entry_time,
        trade_id=trade.trade_id,
        plan_id=trade.plan_id,
        setup_id=trade.setup_id,
        direction=trade.direction,
        entry=entry_price,
        size_pct=size_pct,
        margin_usd=margin_usd,
        notional_usd=notional_usd,
        leverage=leverage,
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
    notional_usd = (
        float(trade.notional_usd) if trade.notional_usd is not None else equity_usd * size_pct
    )
    leg_usd = round(notional_usd * partial.frac * partial.pnl_pct, 2)
    # Mirror model: fire a reduce-only market order for the scaled-out fraction.
    leg_order_id: str | None = None
    ctx = live_ctx.get()
    if ctx is not None and trade.venue == "binance_testnet":
        result = await ctx.reduce_position(
            trade.symbol,
            trade.direction,
            notional_usd=notional_usd,
            entry_price=float(trade.entry_price),
            frac=partial.frac,
        )
        leg_order_id = result.order_id if result is not None else None
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
            "order_id": leg_order_id,
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
    prev = (trade.trade_metadata or {}).get("stop_working")
    _merge_state(trade, state)
    await session.flush()
    # Engine-synced: if the working stop moved (breakeven / trail), cancel + replace the
    # exchange STOP_MARKET so the on-exchange protection tracks the engine's stop.
    ctx = live_ctx.get()
    if (
        ctx is not None
        and trade.venue == "binance_testnet"
        and state.working_stop is not None
        and state.working_stop != prev
    ):
        await ctx.replace_stop(str(trade_id), trade.symbol, trade.direction, state.working_stop)


async def close_paper_trade(
    session: AsyncSession,
    trade_id: uuid.UUID,
    exit_result: ExitResult,
    *,
    equity_usd: float,
    size_pct: float,
) -> ExitResult | None:
    """Mark a trade closed and record realized pnl.

    ``exit_result.pnl_pct`` is the internal return on notional. Persisted ``pnl_pct`` is
    return on margin, i.e. ``pnl_usd / margin_usd``.
    """
    trade = await session.get(PaperTrade, trade_id)
    if trade is None:
        log.warning("paper_trade_missing_on_close", trade_id=str(trade_id))
        return None
    notional_usd = (
        float(trade.notional_usd) if trade.notional_usd is not None else equity_usd * size_pct
    )
    if trade.margin_usd is not None:
        margin_usd = float(trade.margin_usd)
    elif trade.leverage is not None and float(trade.leverage) > 0:
        margin_usd = notional_usd / float(trade.leverage)
    else:
        margin_usd = notional_usd
    pnl_usd, margin_pnl_pct = margin_close_values(
        notional_usd=notional_usd, margin_usd=margin_usd, notional_pnl_pct=exit_result.pnl_pct
    )
    # Engine-synced close: cancel the resting SL/TP first (so they can't fire against the
    # close), then market-close the FULL remaining testnet position (reduce-only).
    ctx = live_ctx.get()
    if ctx is not None and trade.venue == "binance_testnet":
        await ctx.cancel_protection(str(trade_id), trade.symbol)
        result = await ctx.close_position(trade.symbol, trade.direction)
        if result is not None:
            trade.exit_order_id = result.order_id
    trade.exit_price = exit_result.exit_price
    trade.exit_time = exit_result.exit_time
    trade.exit_reason = exit_result.exit_reason
    trade.pnl_pct = margin_pnl_pct
    trade.pnl_usd = pnl_usd
    trade.status = "closed"
    await session.flush()
    log.info(
        "paper_trade_closed",
        trade_id=str(trade_id),
        reason=exit_result.exit_reason,
        pnl_pct=round(margin_pnl_pct, 4),
    )
    closed = ExitResult(
        exit_result.exit_price, exit_result.exit_time, exit_result.exit_reason, margin_pnl_pct
    )
    trace.trade_closed(
        now=exit_result.exit_time,
        trade_id=trade_id,
        plan_id=trade.plan_id,
        reason=exit_result.exit_reason,
        pnl_pct=closed.pnl_pct,
    )
    return closed
