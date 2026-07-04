"""Engine orchestration: replay, single-tick, and live-poll loops.

All three share the same per-bar logic (refresh the plan when stale → ``evaluate_now``).
Replay is the primary POC demo: it walks historical feature rows in time order as if
each were "now". The code path is identical to live; only the source of "now" differs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ats.config import settings
from ats.engine import state
from ats.engine.orchestrator import evaluate_now
from ats.engine.reports import ReplayReport, TickReport, TradeOutcome
from ats.engine.timeframes import timeframe_to_timedelta
from ats.execution.executor import RunTag, current_run
from ats.llm.client import LlmClient
from ats.logging import get_logger
from ats.planning.create_plan import create_plan

log = get_logger(__name__)


def _plan_bar_open(now: datetime, plan_dt: timedelta) -> datetime:
    """Open_time of the most-recent CLOSED plan-tf bar as of ``now`` (UTC, epoch-aligned).

    Crypto bars are epoch-aligned to the bar length, so the bar with ``open_time = T`` closes at
    ``T + plan_dt``. As of ``now`` the most-recent CLOSED plan-tf bar has
    ``open_time = floor(now / plan_dt) * plan_dt - plan_dt``: at ``now = 02:15`` on 1h bars that's
    the 01:00 bar (closed at 02:00); at ``now = 02:00`` exactly that's still the 01:00 bar. Used by
    the hybrid plan/decision split to detect plan-tf boundaries from finer decision-tf rows.
    """
    bar_s = int(plan_dt.total_seconds())
    epoch = int(now.timestamp())
    last_closed_epoch = (epoch // bar_s) * bar_s - bar_s
    return datetime.fromtimestamp(last_closed_epoch, tz=UTC)


# Per-bar status notes that persist across many consecutive bars. We collapse these
# to a single line at the transition (start / resume) instead of one line per bar.
_STICKY_NOTES = frozenset(
    {"position open: skipping new entries", "soft invalidation: new entries paused"}
)


def _accumulate(report: ReplayReport, tick: TickReport) -> None:
    report.detections += tick.detections
    report.opened += tick.opened
    report.closed += tick.closed
    report.confirm_calls += tick.confirm_calls
    report.observe_calls += tick.observe_calls
    report.risk_rejected += tick.risk_rejected
    if tick.plan_invalidated:
        report.invalidations += 1
    for ct in tick.closed_trades:
        report.trade_outcomes.append(
            TradeOutcome(
                direction=ct.direction,
                entry_price=ct.entry_price,
                stop_loss=ct.stop_loss,
                exit_price=ct.exit_price,
                exit_reason=ct.exit_reason,
                margin_usd=ct.margin_usd,
                notional_usd=ct.notional_usd,
                leverage=ct.leverage,
                pnl_pct=ct.pnl_pct,
                pnl_usd=ct.pnl_usd,
                atr_at_entry=ct.atr_at_close,  # best proxy we have; entry ATR is not threaded
                stop_dist_pts=abs(ct.entry_price - ct.stop_loss),
            )
        )


def _record_notes(report: ReplayReport, tick: TickReport, active_sticky: str | None) -> str | None:
    """Append this tick's notes, collapsing sticky states to start/resume transitions.

    Returns the sticky state now active (carried into the next bar).
    """
    if tick.now is None:
        return active_sticky
    ts = str(tick.now)[:19]
    sticky = next((n for n in tick.notes if n in _STICKY_NOTES), None)
    for n in tick.notes:
        if n not in _STICKY_NOTES:
            report.notes.append(f"{ts} {n}")
    if sticky != active_sticky:
        if sticky is not None:
            report.notes.append(f"{ts} {sticky}")
        elif active_sticky is not None:
            report.notes.append(f"{ts} entries resumed")
    return sticky


async def _ensure_plan(
    session: AsyncSession,
    client: LlmClient,
    *,
    symbol: str,
    tf: str,
    now: datetime,
    bars_since_plan: int,
    run_id: str | None = None,
    crossed_plan_boundary: bool = True,
) -> tuple[bool, int, int]:
    """Refresh the plan if there is none, it expired, or the refresh cadence elapsed.

    Never refreshes while a position is open: re-thinking the thesis mid-trade is the
    exit manager's / invalidation rules' job, not the strategist's, and a plan built
    while holding would be discarded unused (entries are blocked) — wasting an LLM call.

    ``bars_since_plan`` counts in PLAN-tf bars: under the hybrid plan/decision split it is only
    incremented when ``crossed_plan_boundary`` is True (a new plan-tf bar just closed), so
    ``plan_refresh_bars`` keeps its plan-tf semantics regardless of the decision tf. Defaults True
    so the single-tf path (caller walks plan-tf rows, every row is a boundary) is byte-unchanged.

    Returns (created, bars_since_plan, setup_count).
    """
    if await state.open_positions(session, symbol=symbol, run_id=run_id):
        return False, bars_since_plan, 0
    plan = await state.active_plan(session, symbol, run_id=run_id)
    stale = (
        plan is None
        or now >= plan.expires_at
        or bars_since_plan >= settings.plan.plan_refresh_bars
    )
    if not stale and settings.plan.replan_on_regime_change and plan is not None:
        # Re-plan when the world changes (#6): the regime cell that anchored this plan flipped,
        # so its thesis is stale even though the refresh timer hasn't elapsed.
        regime = await state.latest_regime(session, before_ts=now)
        current_cell = (regime or {}).get("regime_cell")
        if current_cell is not None and current_cell != plan.regime_cell:
            stale = True
    if stale:
        result = await create_plan(session, client, symbol=symbol, tf=tf, as_of=now, run_id=run_id)
        return True, 0, len(result.setups)
    return False, (bars_since_plan + 1) if crossed_plan_boundary else bars_since_plan, 0


async def run_replay(
    session: AsyncSession,
    client: LlmClient,
    *,
    symbol: str,
    tf: str,
    since: datetime,
    until: datetime | None = None,
    run: RunTag | None = None,
    decision_tf: str | None = None,
) -> ReplayReport:
    """Walk historical feature rows, running the full loop on each as if live.

    ``run`` tags every trade opened during the walk (run_id / run_label / config_hash) so
    overlapping replay windows stay isolated and A/B-comparable.

    ``tf`` is the PLAN timeframe (planner cadence + plan features). ``decision_tf`` (defaults to
    ``tf`` for the historic single-tf behavior) is the DECISION timeframe — the bar cadence the
    replay walks and on which ``evaluate_now`` runs (entry detection + exit reconciliation). When
    set finer than ``tf``, plans refresh on ``tf`` boundaries (every ``plan_refresh_bars`` plan-tf
    bars) while entries/exits monitor every decision-tf bar. ``max_hold_bars`` counts in
    decision-tf bars.
    """
    dec_tf = decision_tf or tf
    rows = await state.feature_rows_since(session, symbol, dec_tf, since, until=until)
    report = ReplayReport(symbol=symbol, timeframe=dec_tf)
    if not rows:
        report.notes.append("no feature rows in window")
        return report

    token = current_run.set(run)
    try:
        return await _walk_replay(
            session, client, symbol=symbol, plan_tf=tf, decision_tf=dec_tf,
            rows=rows, report=report,
        )
    finally:
        current_run.reset(token)


async def _walk_replay(
    session: AsyncSession,
    client: LlmClient,
    *,
    symbol: str,
    plan_tf: str,
    decision_tf: str,
    rows: list,
    report: ReplayReport,
) -> ReplayReport:
    run = current_run.get()
    run_id = run.run_id if run else None
    bars_since_plan = settings.plan.plan_refresh_bars  # force a plan on the first bar
    active_sticky: str | None = None
    running_equity = settings.risk.paper_equity_usd
    observe_tf = settings.observer.observe_timeframe
    base_dt = timeframe_to_timedelta(decision_tf)
    obs_dt = timeframe_to_timedelta(observe_tf) if observe_tf != decision_tf else base_dt
    # Hybrid plan/decision split: detect plan-tf boundaries from the (finer) decision-tf rows so
    # `plan_refresh_bars` keeps its plan-tf semantics. When decision_tf == plan_tf every decision
    # row IS a plan-tf boundary (crossed=True each tick), preserving the historic single-tf path.
    plan_dt = timeframe_to_timedelta(plan_tf)
    hybrid = decision_tf != plan_tf
    last_plan_bar: datetime | None = None
    for i, row in enumerate(rows):
        now = row["open_time"]
        prev = rows[i - 1] if i > 0 else None

        # While a position is open, manage exits on the finer timeframe. The feature row at
        # open_time=now carries OHLC for the bar [now, now+base_dt); fetch the observe-tf
        # candles that actually compose *this* bar so reconciliation (and the observation
        # agent) react within it before invalidation/rules see the bar's close. candles_between
        # is (start, end]: start=now-obs_dt includes the sub-candle at exactly `now`; end at
        # now+base_dt-obs_dt includes the last sub-candle and excludes the next bar's open.
        open_before = bool(await state.open_positions(session, symbol=symbol, run_id=run_id))
        fine_candles = None
        if open_before and prev is not None and observe_tf != decision_tf:
            fine_candles = await state.candles_between(
                session, symbol, observe_tf, now - obs_dt, now + base_dt - obs_dt
            )

        # Plan-tf boundary detection: a new plan-tf bar has closed since the last row when the
        # most-recent-closed plan-tf bar's open_time advanced. On the first row `last_plan_bar`
        # is None → treat as a boundary so a plan is forced immediately.
        crossed_plan_boundary = True
        if hybrid:
            current_plan_bar = _plan_bar_open(now, plan_dt)
            crossed_plan_boundary = last_plan_bar is None or current_plan_bar != last_plan_bar
            if crossed_plan_boundary:
                last_plan_bar = current_plan_bar

        created, bars_since_plan, setup_count = await _ensure_plan(
            session, client, symbol=symbol, tf=plan_tf, now=now, bars_since_plan=bars_since_plan,
            run_id=run_id, crossed_plan_boundary=crossed_plan_boundary,
        )
        if created:
            report.plans_created += 1
            if setup_count > 0:
                report.plans_with_setups += 1
        tick = await evaluate_now(
            session, client, symbol=symbol, tf=decision_tf, feature_row=row, prev_row=prev,
            candle_closed=True, now=now, fine_candles=fine_candles, run_id=run_id,
            equity_usd=running_equity,
        )
        _accumulate(report, tick)
        for ct in tick.closed_trades:
            running_equity += ct.pnl_usd
        active_sticky = _record_notes(report, tick, active_sticky)
        report.bars += 1

        # Re-plan on close: the thesis that anchored the just-closed trade is stale, so
        # discard the active plan and force a fresh one on the next bar.
        if (
            settings.plan.replan_on_close
            and tick.closed > 0
            and await state.supersede_active_plan(session, symbol, run_id=run_id)
        ):
            bars_since_plan = settings.plan.plan_refresh_bars

    await session.commit()
    report.finalise(starting_equity=settings.risk.paper_equity_usd)
    return report


async def run_tick(
    session: AsyncSession, client: LlmClient, *, symbol: str, tf: str
) -> TickReport:
    """Single evaluation against the most recent feature row."""
    latest = await state.latest_feature_row(session, symbol, tf)
    if latest is None:
        raise ValueError(f"no features for {symbol} {tf}")
    now = latest["open_time"]
    # previous bar (for crossing operators)
    prev_q = await session.execute(
        text(
            "SELECT open_time FROM features WHERE symbol=:s AND timeframe=:tf "
            "AND open_time < :now ORDER BY open_time DESC LIMIT 1"
        ),
        {"s": symbol, "tf": tf, "now": now},
    )
    prev_ts = prev_q.scalar()
    prev = await state.latest_feature_row(session, symbol, tf, as_of=prev_ts) if prev_ts else None

    # Live is stateless per tick (no loop-carried counter), so the refresh cadence rides on
    # the active plan's own ``expires_at`` (= created + plan_refresh_bars). Passing 0 keeps the
    # bars_since_plan term of the staleness check permanently false, so a plan is only refreshed
    # when it actually expires (or is superseded on close / regime change) — NOT every bar. The
    # old ``plan_refresh_bars`` here made the term always true, re-planning every bar and
    # orphaning wick_limit setups armed on the prior bar before they could fill.
    await _ensure_plan(
        session, client, symbol=symbol, tf=tf, now=now,
        bars_since_plan=0,
    )
    tick = await evaluate_now(
        session, client, symbol=symbol, tf=tf, feature_row=latest, prev_row=prev,
        candle_closed=True, now=now,
    )
    if settings.plan.replan_on_close and tick.closed > 0:
        await state.supersede_active_plan(session, symbol)
    await session.commit()
    return tick


async def run_live(
    session_factory: Callable[[], AsyncSession],
    client: LlmClient,
    *,
    symbol: str,
    tf: str,
    interval: int,
    once: bool = False,
    refresh_fn: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Poll loop: optionally refresh data, then run a tick, sleeping ``interval`` seconds.

    Not tick-precise (spec 01 has no websocket); ``refresh_fn`` is expected to backfill
    candles + recompute features before each tick.
    """
    while True:
        if refresh_fn is not None:
            await refresh_fn()
        async with session_factory() as session:
            tick = await run_tick(session, client, symbol=symbol, tf=tf)
        log.info(
            "live_tick",
            symbol=symbol,
            detections=tick.detections,
            opened=tick.opened,
            closed=tick.closed,
            risk_rejected=tick.risk_rejected,
            notes=tick.notes,
        )
        if once:
            return
        await asyncio.sleep(interval)
