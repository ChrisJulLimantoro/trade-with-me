"""Orchestrator — wires the engine collaborators for one evaluation tick.

For a given "now" (a feature row), against the active plan it:
1. reconciles open trades against the current bar (exit machine + observer),
2. evaluates invalidation rules → may kill the plan and close open trades,
3. for each armed setup, runs the rule engine; on detection it sizes and opens a paper
   trade deterministically (the per-setup LLM confirm was retired in Part 2).

This module owns no exit/observer/entry/close logic itself — it depends on the focused
collaborators (``exits.manager``, ``invalidation``, ``entries``) and composes them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ats import trace
from ats.config import settings
from ats.engine import state
from ats.engine.closing import open_trades_for
from ats.engine.entries import execute_setup, setup_dict
from ats.engine.exits.manager import advance_trade, reconcile_open_trades
from ats.engine.invalidation import handle_invalidation
from ats.engine.quantities import _f
from ats.engine.reports import ClosedTradeInfo, TickReport
from ats.engine.rule_engine import entry_confirmed, evaluate_setup
from ats.engine.timeframes import timeframe_to_timedelta
from ats.execution.executor import poll_live_entries
from ats.execution.live import live_ctx
from ats.llm.client import LlmClient
from ats.logging import get_logger
from ats.risk.manager import regime_allows

__all__ = ["ClosedTradeInfo", "TickReport", "evaluate_now"]

log = get_logger(__name__)


def _post_fill_candles(
    candles: list[dict[str, Any]],
    zone_low: Any,
    zone_high: Any,
) -> list[dict[str, Any]]:
    """Sub-candles strictly AFTER the one whose range first touches the entry zone (the fill).

    A wick-limit entry fills the moment price first trades into the zone. Order WITHIN that
    sub-candle is unknowable, so we assume fill-then-hold and only exit-check the sub-candles
    that follow it. This catches an immediate post-fill stop-out without inventing a target hit
    on a pre-fill spike (which would be the same look-ahead bias in reverse). Returns ``[]``
    when the zone is unknown or the window never touches it.
    """
    if zone_low is None or zone_high is None:
        return []
    lo, hi = float(zone_low), float(zone_high)
    for i, c in enumerate(candles):
        if float(c["low"]) <= hi and float(c["high"]) >= lo:
            return candles[i + 1 :]
    return []


def _live_entry_pending(symbol: str) -> bool:
    """True when live execution has a resting-limit entry for ``symbol`` awaiting a fill.

    A pending entry blocks placing another (one position per symbol) and stops the per-tick
    setup loop after it arms one resting order. No-op (False) in replay/paper (no live_ctx).
    """
    ctx = live_ctx.get()
    return ctx is not None and ctx.has_pending(symbol)


def _arm_limit_price(direction: str, zone_low: float, zone_high: float) -> float:
    """The resting-limit price for a setup: the zone edge a fill is *worst* at.

    Long rests at the zone HIGH (the highest price a buyer accepts), short at the zone LOW.
    This is the same conservative edge the legacy wick-limit used for the fill price — only
    the *timing* changes (the order now rests to a later bar instead of filling on the bar
    that armed it), which is what removes the look-ahead.
    """
    return zone_low if str(direction).lower() == "short" else zone_high


def _entry_gates_ok(
    plan: Any,
    setup: Any,
    feature_row: dict[str, Any],
    prev_row: dict[str, Any] | None,
    report: TickReport,
) -> bool:
    """Non-price entry gates: regime-direction filter + optional bar-close confirmation.

    Shared by the close-mode fill and the wick-mode arm decision so both honor the same
    discipline. Appends a skip note + trace outcome on rejection; returns False to skip.
    """
    # Enforce the direction gate the strategist planned under: the planner persists the exact
    # allowed_directions (incl. any HTF-exhaustion counter-trend relief) onto the plan, so
    # honor that; fall back to the strict trend-only gate for plans predating the field.
    allowed = (getattr(plan, "plan_metadata", None) or {}).get("allowed_directions")
    if allowed is not None:
        direction_ok = setup.direction in allowed
    else:
        direction_ok = regime_allows(plan.regime_cell, setup.direction)
    if settings.plan.regime_filter and not direction_ok:
        trace.outcome(f"SKIPPED: regime {plan.regime_cell} disallows {setup.direction}")
        report.notes.append(
            f"setup {setup.setup_id} regime-filtered ({plan.regime_cell}/{setup.direction})"
        )
        return False
    if settings.plan.entry_confirmation_enabled:
        confirmed, reason = entry_confirmed(setup.direction, feature_row, prev_row)
        if not confirmed:
            trace.outcome(f"AWAITING confirmation: {reason}")
            report.notes.append(f"setup {setup.setup_id} awaiting confirmation ({reason})")
            return False
    return True


async def _reconcile_entry_bar(
    session: AsyncSession,
    client: LlmClient,
    *,
    symbol: str,
    tf: str,
    now: datetime,
    feature_row: dict[str, Any],
    report: TickReport,
    run_id: str | None,
) -> None:
    """Exit-check a just-opened trade over the REST of its own entry bar.

    Without this, a trade opened on the decision bar is first reconciled on the NEXT bar, so a
    fill that is immediately stopped out inside its own bar is recorded as still-open at a
    favorable mark — the worst same-bar outcomes never appear in results, biasing every replay
    upward. We replay the entry bar's finer sub-candles AFTER the fill so that stop-out is
    booked on the bar it actually happened.

    Reconstructing intrabar order needs the finer observe timeframe; when none is configured
    (``observe_timeframe == tf``) the entry bar stays unresolved — the honest limitation.
    """
    observe_tf = settings.observer.observe_timeframe
    if observe_tf == tf:
        return
    base_dt = timeframe_to_timedelta(tf)
    obs_dt = timeframe_to_timedelta(observe_tf)
    # Same window the live/replay loop uses for a bar's sub-candles: (now-obs_dt,
    # now+base_dt-obs_dt] spans exactly the finer bars composing [now, now+base_dt).
    sub_candles = await state.candles_between(
        session, symbol, observe_tf, now - obs_dt, now + base_dt - obs_dt
    )
    if not sub_candles:
        return
    raw_atr = feature_row.get("atr_14")
    atr = _f(raw_atr) if raw_atr is not None else None
    for trade in await open_trades_for(session, symbol, run_id=run_id):
        if trade.entry_time < now:
            continue  # opened on a prior bar — already managed by the top-of-tick reconcile
        md = trade.trade_metadata or {}
        post = _post_fill_candles(sub_candles, md.get("entry_zone_low"), md.get("entry_zone_high"))
        if not post:
            continue
        closed_before = report.closed
        await advance_trade(
            session, client, trade, post, report,
            atr=atr, feature_row=feature_row, intrabar=True,
        )
        if report.closed > closed_before:
            log.info(
                "entry_bar_exit",
                trade_id=str(trade.trade_id),
                symbol=symbol,
                direction=trade.direction,
                sub_candles=len(post),
            )
            report.notes.append(
                f"entry-bar exit: trade {str(trade.trade_id)[:8]} closed on its own bar"
            )


async def evaluate_now(
    session: AsyncSession,
    client: LlmClient,
    *,
    symbol: str,
    tf: str,
    feature_row: dict[str, Any],
    prev_row: dict[str, Any] | None,
    candle_closed: bool = True,
    now: datetime | None = None,
    fine_candles: list[dict[str, Any]] | None = None,
    run_id: str | None = None,
    equity_usd: float | None = None,
) -> TickReport:
    """Run one full evaluation of the active plan against the current feature row.

    When ``fine_candles`` is supplied (a position is open and a finer timeframe is
    configured), open trades are reconciled bar-by-bar over those candles with the
    observation agent in the loop; otherwise the single decision-bar candle is used.
    ``run_id`` isolates replay runs so concurrent same-symbol replays don't see each
    other's plans or trades.
    """
    now = now or feature_row["open_time"]
    report = TickReport(now=now)

    # Live only (no-op in replay/paper): resolve resting-limit entries placed on a prior tick —
    # book the ones that filled (so they're reconciled below) and drop the ones that timed out —
    # BEFORE reconciling/evaluating, so a just-filled entry is managed the same tick it fills.
    await poll_live_entries(session, symbol, now)

    candle = {
        "open_time": feature_row["open_time"],
        "high": feature_row["high"],
        "low": feature_row["low"],
        "close": feature_row["close"],
    }
    atr = feature_row.get("atr_14")
    await reconcile_open_trades(
        session, symbol, candle, report,
        atr=_f(atr) if atr is not None else None,
        client=client, fine_candles=fine_candles, feature_row=feature_row,
        run_id=run_id,
    )

    plan = await state.active_plan(session, symbol, run_id=run_id)
    if plan is None:
        report.notes.append("no active plan")
        return report
    report.plan_id = plan.plan_id

    if now >= plan.expires_at:
        plan.status = "expired"
        await session.flush()
        report.notes.append("plan expired")
        return report

    setups = await state.plan_setups(session, plan.plan_id)
    killed = await handle_invalidation(
        session, client, plan, setups, feature_row, prev_row, candle_closed, report,
        run_id=run_id,
    )
    if killed or report.paused:
        return report

    open_positions = await state.open_positions(session, symbol=symbol, run_id=run_id)
    if open_positions or _live_entry_pending(symbol):
        # One position per symbol: no new entry is possible this bar. Existing trades are still
        # reconciled above; a live resting-limit entry already placed (pending fill) likewise
        # blocks a second entry until it fills or times out. Any armed setups keep waiting.
        report.notes.append("position open: skipping new entries")
        return report

    wick_mode = settings.plan.entry_trigger_mode == "wick_limit"

    # Phase 1 (wick_limit) — fill resting limit orders ARMED on a PRIOR bar. The thesis was
    # validated at arm time on a closed bar; a later bar trading through the limit fills it at
    # the limit price. This is a genuine resting order: the fill never consults the filling
    # bar's own close, which is exactly the same-bar look-ahead the old synchronous wick fill
    # had (it both decided on and filled inside the same closed bar). Earliest fill is the bar
    # after the arm, because a freshly-armed setup is only ``armed`` after this phase runs.
    if wick_mode:
        for setup in setups:
            if setup.status != "armed":
                continue
            if now >= setup.expires_at:
                setup.status = "expired"
                continue
            limit = _arm_limit_price(
                setup.direction, _f(setup.entry_zone_low), _f(setup.entry_zone_high)
            )
            low, high = _f(feature_row["low"]), _f(feature_row["high"])
            if not (low <= limit <= high):  # price never traded through the resting limit
                continue
            opened_before = report.opened
            await execute_setup(
                session, plan, setup, setup_dict(setup), limit, feature_row, now,
                open_positions, report, tf=tf, equity_usd=equity_usd,
            )
            if report.opened > opened_before:
                log.info(
                    "armed_fill",
                    setup_id=str(setup.setup_id),
                    symbol=symbol,
                    direction=setup.direction,
                    limit=limit,
                )
                report.notes.append(
                    f"setup {setup.setup_id} resting-limit filled at {limit:g}"
                )
                break
            if _live_entry_pending(symbol):
                break  # a live resting-limit entry was placed — don't arm a second this tick

    if not report.opened:
        for setup in setups:
            if setup.status != "active":
                continue
            if now >= setup.expires_at:
                setup.status = "expired"
                continue
            setup_d = setup_dict(setup)
            ev = evaluate_setup(
                setup_d,
                feature_row,
                prev_row,
                soft_threshold=settings.plan.soft_threshold,
                entry_trigger_mode=settings.plan.entry_trigger_mode,
            )
            if wick_mode:
                # ARM a resting limit: the thesis (hard + soft) must pass on THIS closed bar,
                # but the fill is deferred to a later bar that trades through the zone edge
                # (Phase 1). Price location this bar is irrelevant — the order just rests.
                if not (ev.hard_ok and ev.soft_score >= settings.plan.soft_threshold):
                    continue
                report.detections += 1
                if not _entry_gates_ok(plan, setup, feature_row, prev_row, report):
                    continue
                limit = _arm_limit_price(
                    setup.direction, _f(setup.entry_zone_low), _f(setup.entry_zone_high)
                )
                setup.status = "armed"
                setup.setup_metadata = {
                    **(setup.setup_metadata or {}),
                    "armed_at": now.isoformat(),
                    "armed_limit": limit,
                }
                log.info(
                    "setup_armed",
                    setup_id=str(setup.setup_id),
                    symbol=symbol,
                    direction=setup.direction,
                    limit=limit,
                )
                report.notes.append(
                    f"setup {setup.setup_id} armed resting limit at {limit:g}"
                )
                continue

            # close mode — market-on-close: the decision bar's close is in the zone, fill at
            # it. Deciding on and filling at the same close is executable (no look-ahead).
            if not ev.detected:
                continue
            report.detections += 1
            if not _entry_gates_ok(plan, setup, feature_row, prev_row, report):
                continue
            opened_before = report.opened
            await execute_setup(
                session, plan, setup, setup_d, ev.price, feature_row, now,
                open_positions, report, tf=tf, equity_usd=equity_usd,
            )
            if report.opened > opened_before or _live_entry_pending(symbol):
                break

    # A trade opened above is otherwise blind until the next bar; walk the rest of its own
    # entry bar so a same-bar stop-out is booked now, not silently skipped.
    if report.opened:
        await _reconcile_entry_bar(
            session, client, symbol=symbol, tf=tf, now=now,
            feature_row=feature_row, report=report, run_id=run_id,
        )

    await session.flush()
    return report
