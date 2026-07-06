"""Paper executor — records simulated trades. NEVER places a live order.

This is the only component that writes ``paper_trades``. There is intentionally no
exchange client here: the system is paper-only by design.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ats import trace
from ats.db.models import PaperTrade
from ats.execution.live import live_ctx
from ats.execution.reconcile import ExitResult, PartialFill, TradeState
from ats.logging import get_logger

log = get_logger(__name__)

# Returned by ``open_paper_trade`` when live execution placed a RESTING LIMIT entry: no trade is
# booked yet — ``poll_live_entries`` books it on the real fill (or drops it on timeout). Distinct
# from ``None`` (order rejected/too small → the setup is done) so the caller can consume the setup
# without recording a filled trade or an open position.
PENDING = object()

# Exit reasons the exchange already holds a resting order for (STOP_MARKET / TAKE_PROFIT_MARKET,
# incl. trailed/breakeven stops and forced liquidation). For these the native order does the fill,
# so we don't race it with a lagged market close; engine-side reasons (invalidation / expiry /
# observe_exit) have no resting order and must be market-closed.
_NATIVE_EXIT_REASONS = frozenset({"sl", "tp", "breakeven", "trail", "liquidation"})


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
    trade_metadata: dict[str, Any] | None = None,
    entry_expires_at: datetime | None = None,
) -> uuid.UUID | object | None:
    """Open a paper trade (or place its live resting entry) for a risk-approved setup.

    ``setup`` is a dict with setup_id, plan_id, symbol, direction, stop_loss, take_profit.
    ``size_pct`` is notional / equity; ``margin_usd`` is committed capital before leverage, and
    ``notional_usd`` is market exposure after leverage. ``trade_metadata`` is the exit-policy /
    entry-zone / time-stop metadata set at insert time.

    Return values:
    - **pure paper** (``live_ctx`` is None): the row is booked synchronously and its ``trade_id``
      is returned — byte-identical to the historical behavior.
    - **live** (``live_ctx`` set): a RESTING LIMIT is placed at ``entry_price`` (the zone edge the
      sim assumes) and :data:`PENDING` is returned. No row is booked here — ``poll_live_entries``
      books it when the exchange fills the limit (real fill price → ``entry_fill_price``) or drops
      it on the ``entry_expires_at`` timeout. ``None`` is returned only when the live order was
      rejected / too small.
    """
    run = current_run.get()
    ctx = live_ctx.get()
    if ctx is not None:
        # Make-live-match-sim: rest a real LIMIT at the zone edge instead of crossing with a
        # market order. Booking is deferred to the fill (poll_live_entries), so the internal row
        # records the price the exchange ACTUALLY filled — closing the one-way-mirror gap.
        if not notional_usd or notional_usd <= 0:
            log.warning("live_open_no_notional", symbol=setup["symbol"])
            trace.outcome("SKIPPED: live entry has no notional to size an order")
            return None
        res = await ctx.place_entry_limit(
            setup["symbol"],
            setup["direction"],
            notional_usd=float(notional_usd),
            entry_price=entry_price,
            leverage=leverage,
        )
        if res is None:
            trace.outcome("SKIPPED: live testnet entry order rejected/too small")
            return None
        ctx.register_pending(
            str(res.order_id),
            {
                "order_id": str(res.order_id),
                "symbol": setup["symbol"],
                "direction": setup["direction"],
                "target_qty": res.qty,
                "limit_price": entry_price,  # simulated fill (analytics source of truth)
                "expires_at": entry_expires_at,
                # Full booking payload replayed by poll_live_entries when the fill lands:
                "setup_id": setup["setup_id"],
                "plan_id": setup["plan_id"],
                "size_pct": size_pct,
                "leverage": leverage,
                "margin_usd": margin_usd,
                "notional_usd": notional_usd,
                "liq_price": liq_price,
                "risk_usd": risk_usd,
                "entry_time": entry_time,
                "stop_loss": float(setup["stop_loss"]),
                "take_profit": [float(x) for x in setup["take_profit"]],
                "reasons": reasons,
                "trade_metadata": dict(trade_metadata or {}),
                "run_id": run.run_id if run else None,
                "run_label": run.run_label if run else None,
                "config_hash": run.config_hash if run else None,
            },
        )
        trace.outcome(f"RESTING LIMIT placed @ {entry_price:g} (order {res.order_id})")
        return PENDING
    # Pure-paper path — book synchronously exactly as before.
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
        venue="paper",
        entry_order_id=None,
        trade_metadata=dict(trade_metadata or {}),
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


async def _book_live_fill(
    session: AsyncSession,
    ctx: Any,
    rec: dict[str, Any],
    *,
    fill_price: float,
    filled_qty: float,
) -> uuid.UUID:
    """Book the internal PaperTrade for a filled resting-limit entry, then place protection.

    ``entry_price`` stays the simulated zone edge (``limit_price``) so the analytics/exit machine
    are unchanged, while ``entry_fill_price`` records the real exchange fill. A PARTIAL fill scales
    the sizing (margin/notional/risk/size_pct) by the fill ratio so the booked position matches
    what actually landed.
    """
    target_qty = float(rec.get("target_qty") or 0.0)
    ratio = min(1.0, max(0.0, filled_qty / target_qty)) if target_qty > 0 else 1.0

    def _scale(v: float | None) -> float | None:
        return round(float(v) * ratio, 6) if v is not None else None

    trade = PaperTrade(
        trade_id=uuid.uuid4(),
        setup_id=rec["setup_id"],
        plan_id=rec["plan_id"],
        symbol=rec["symbol"],
        direction=rec["direction"],
        size_pct=_scale(rec["size_pct"]),
        leverage=rec["leverage"],
        margin_usd=_scale(rec["margin_usd"]),
        notional_usd=_scale(rec["notional_usd"]),
        liq_price=rec["liq_price"],
        risk_usd=_scale(rec["risk_usd"]),
        entry_price=rec["limit_price"],
        entry_time=rec["entry_time"],
        stop_loss=rec["stop_loss"],
        take_profit=rec["take_profit"],
        status="open",
        reasons=rec["reasons"],
        run_id=rec["run_id"],
        run_label=rec["run_label"],
        config_hash=rec["config_hash"],
        venue="binance_testnet",
        entry_order_id=rec["order_id"],
        entry_fill_price=fill_price,
        trade_metadata=dict(rec.get("trade_metadata") or {}),
    )
    session.add(trade)
    await session.flush()
    tps = rec.get("take_profit") or []
    if tps:
        await ctx.place_protection(
            str(trade.trade_id),
            rec["symbol"],
            rec["direction"],
            stop_loss=rec["stop_loss"],
            take_profit=float(tps[-1]),
        )
    log.info(
        "live_entry_filled",
        trade_id=str(trade.trade_id),
        symbol=rec["symbol"],
        direction=rec["direction"],
        fill_price=fill_price,
        sim_price=rec["limit_price"],
        filled_qty=filled_qty,
        target_qty=target_qty,
        partial=ratio < 1.0,
    )
    trace.trade_opened(
        now=rec["entry_time"],
        trade_id=trade.trade_id,
        plan_id=trade.plan_id,
        setup_id=trade.setup_id,
        direction=trade.direction,
        entry=trade.entry_price,
        size_pct=trade.size_pct,
        margin_usd=trade.margin_usd,
        notional_usd=trade.notional_usd,
        leverage=trade.leverage,
    )
    return trade.trade_id


async def poll_live_entries(session: AsyncSession, symbol: str, now: datetime) -> None:
    """Advance the pending resting-limit entries for ``symbol``: book fills, drop timeouts.

    Runs at the top of every live tick (no-op when ``live_ctx`` is None, i.e. replay/paper). Each
    pending order is polled once: FILLED → book the trade; terminal-with-no-fill → drop; past its
    fill-or-cancel window → cancel (booking any partial that landed); otherwise keep resting.
    """
    ctx = live_ctx.get()
    if ctx is None:
        return
    for rec in ctx.pending_snapshot(symbol):
        order_id = rec["order_id"]
        try:
            info = await ctx.client.get_order(symbol, order_id)
        except Exception as exc:  # a transient poll failure must not break the tick
            log.warning("live_entry_poll_failed", symbol=symbol, order_id=order_id, error=str(exc))
            continue
        status = str(info.get("status", ""))
        filled_qty = float(info.get("executedQty") or 0.0)
        avg = float(info.get("avgPrice") or 0.0)
        fill_price = avg if avg > 0 else float(rec["limit_price"])
        expires_at = rec.get("expires_at")
        timed_out = expires_at is not None and now >= expires_at

        if status == "FILLED" and filled_qty > 0:
            ctx.pop_pending(order_id)
            await _book_live_fill(session, ctx, rec, fill_price=fill_price, filled_qty=filled_qty)
        elif status in ("CANCELED", "EXPIRED", "REJECTED"):
            ctx.pop_pending(order_id)
            if filled_qty > 0:  # a partial landed before it went terminal — book it
                await _book_live_fill(
                    session, ctx, rec, fill_price=fill_price, filled_qty=filled_qty
                )
            else:
                log.info("live_entry_no_fill", symbol=symbol, order_id=order_id, status=status)
                ctx.record("open", {"symbol": symbol, "order_id": order_id, "status": "no_fill"})
        elif timed_out:
            ctx.pop_pending(order_id)
            await ctx.client.cancel_order(symbol, order_id)
            if filled_qty > 0:  # partially filled at timeout — book the partial, cancel remainder
                await _book_live_fill(
                    session, ctx, rec, fill_price=fill_price, filled_qty=filled_qty
                )
            else:
                log.info("live_entry_timeout_cancel", symbol=symbol, order_id=order_id)
                ctx.record("open", {"symbol": symbol, "order_id": order_id, "status": "timeout"})
        # else: still resting (NEW / PARTIALLY_FILLED within the window) — keep waiting.


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
    leg_fill_price: float | None = None
    ctx = live_ctx.get()
    if ctx is not None and trade.venue == "binance_testnet":
        result = await ctx.reduce_position(
            trade.symbol,
            trade.direction,
            notional_usd=notional_usd,
            entry_price=float(trade.entry_price),
            frac=partial.frac,
            intended_price=partial.price,
        )
        leg_order_id = result.order_id if result is not None else None
        # Reality track: the price the reduce-only market order actually filled at (vs the
        # simulated ``partial.price`` used for the analytics pnl above).
        leg_fill_price = result.avg_price if result is not None else None
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
            "fill_price": leg_fill_price,
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
    # Engine-synced close. For a level the exchange already holds (SL/TP/trail/breakeven/liq) let
    # the NATIVE order do the fill — don't race it with a lagged market close (the old behavior,
    # which at 20x turned a poll-blind-spot fill error into a sign-flip). Only market-close when
    # the position is somehow still open (native hasn't fired) or the exit is engine-driven
    # (invalidation / expiry / observe_exit — no resting order exists for it).
    ctx = live_ctx.get()
    if ctx is not None and trade.venue == "binance_testnet":
        native_reason = exit_result.exit_reason in _NATIVE_EXIT_REASONS
        still_open = await ctx.client.position_risk(trade.symbol) if native_reason else None
        exit_fill_price: float | None = None
        await ctx.cancel_protection(str(trade_id), trade.symbol)
        if native_reason and still_open is None:
            # Native STOP/TP already closed it — no market lag to reconcile.
            log.info("live_exit_native", trade_id=str(trade_id), reason=exit_result.exit_reason)
        else:
            result = await ctx.close_position(
                trade.symbol, trade.direction, intended_price=exit_result.exit_price
            )
            if result is not None:
                trade.exit_order_id = result.order_id
                exit_fill_price = result.avg_price or None
        # Reconcile the paper PnL against what the exchange ACTUALLY booked for this trade —
        # realized PnL, commissions, funding — and persist it onto the reality-track columns (not
        # just a log line). A failed income pull must never break the close.
        try:
            entry_dt = trade.entry_time
            if entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=UTC)
            start_ms = int(entry_dt.timestamp() * 1000) - 60_000  # 1-min buffer for clock skew
            costs = await ctx.client.realized_costs(trade.symbol, start_ms)
            trade.realized_pnl_usd = costs["realized_pnl"]
            trade.commission_usd = costs["commission"]
            if exit_fill_price is not None:
                trade.exit_fill_price = exit_fill_price
            log.info(
                "live_pnl_reconciled",
                trade_id=str(trade_id),
                paper_pnl_usd=pnl_usd,
                realized_pnl_usd=costs["realized_pnl"],
                commission_usd=costs["commission"],
                funding_usd=costs["funding"],
                exit_fill_price=exit_fill_price,
                net_realized_usd=round(
                    costs["realized_pnl"] + costs["commission"] + costs["funding"], 6
                ),
            )
        except Exception as exc:  # observability only — never break the close
            log.warning("live_pnl_reconcile_failed", trade_id=str(trade_id), error=str(exc))
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
