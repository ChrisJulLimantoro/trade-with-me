"""Engine orchestration: replay, single-tick, and live-poll loops.

All three share the same per-bar logic (refresh the plan when stale → ``evaluate_now``).
Replay is the primary POC demo: it walks historical feature rows in time order as if
each were "now". The code path is identical to live; only the source of "now" differs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ats.config import settings
from ats.engine import state
from ats.engine.detector import ClosedTradeInfo, TickReport, evaluate_now
from ats.engine.timeframes import timeframe_to_timedelta
from ats.execution.executor import RunTag, current_run
from ats.llm.client import LlmClient
from ats.logging import get_logger
from ats.planning.create_plan import create_plan

log = get_logger(__name__)


@dataclass
class TradeOutcome:
    """Lightweight summary captured for each closed trade during replay."""

    direction: str
    entry_price: float
    stop_loss: float
    exit_price: float
    exit_reason: str
    margin_usd: float
    notional_usd: float
    leverage: float | None
    pnl_pct: float  # return on committed margin, signed
    pnl_usd: float
    atr_at_entry: float | None  # atr_14 at the bar we entered
    stop_dist_pts: float  # abs(entry - stop_loss) in price points


@dataclass
class ReplayReport:
    symbol: str
    timeframe: str
    bars: int = 0
    plans_created: int = 0
    plans_with_setups: int = 0
    detections: int = 0
    opened: int = 0
    closed: int = 0
    invalidations: int = 0
    confirm_calls: int = 0
    observe_calls: int = 0
    risk_rejected: int = 0
    trade_outcomes: list[TradeOutcome] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # --- derived metrics (populated by finalise()) ---
    win_rate: float = 0.0
    avg_pnl_pct: float = 0.0  # average return on committed margin
    expectancy_pct: float = 0.0  # margin avg_win * win_rate + avg_loss * loss_rate
    avg_stop_dist_atr: float | None = None  # avg stop distance in ATR multiples

    def finalise(self) -> None:
        """Compute summary metrics from trade_outcomes. Call once after the loop."""
        if not self.trade_outcomes:
            return
        wins = [t for t in self.trade_outcomes if t.pnl_pct > 0]
        losses = [t for t in self.trade_outcomes if t.pnl_pct <= 0]
        n = len(self.trade_outcomes)
        self.win_rate = len(wins) / n
        self.avg_pnl_pct = sum(t.pnl_pct for t in self.trade_outcomes) / n
        avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0.0
        self.expectancy_pct = avg_win * self.win_rate + avg_loss * (1 - self.win_rate)
        atr_multiples = [
            t.stop_dist_pts / t.atr_at_entry
            for t in self.trade_outcomes
            if t.atr_at_entry and t.atr_at_entry > 0
        ]
        if atr_multiples:
            self.avg_stop_dist_atr = sum(atr_multiples) / len(atr_multiples)


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
) -> tuple[bool, int, int]:
    """Refresh the plan if there is none, it expired, or the refresh cadence elapsed.

    Never refreshes while a position is open: re-thinking the thesis mid-trade is the
    exit manager's / invalidation rules' job, not the strategist's, and a plan built
    while holding would be discarded unused (entries are blocked) — wasting an LLM call.

    Returns (created, bars_since_plan, setup_count).
    """
    if await state.open_positions(session, symbol=symbol, run_id=run_id):
        return False, bars_since_plan, 0
    plan = await state.active_plan(session, symbol, run_id=run_id)
    stale = plan is None or now >= plan.expires_at or bars_since_plan >= settings.plan_refresh_bars
    if not stale and settings.replan_on_regime_change and plan is not None:
        # Re-plan when the world changes (#6): the regime cell that anchored this plan flipped,
        # so its thesis is stale even though the refresh timer hasn't elapsed.
        regime = await state.latest_regime(session, before_ts=now)
        current_cell = (regime or {}).get("regime_cell")
        if current_cell is not None and current_cell != plan.regime_cell:
            stale = True
    if stale:
        result = await create_plan(session, client, symbol=symbol, tf=tf, as_of=now, run_id=run_id)
        return True, 0, len(result.setups)
    return False, bars_since_plan + 1, 0


async def run_replay(
    session: AsyncSession,
    client: LlmClient,
    *,
    symbol: str,
    tf: str,
    since: datetime,
    until: datetime | None = None,
    run: RunTag | None = None,
) -> ReplayReport:
    """Walk historical feature rows, running the full loop on each as if live.

    ``run`` tags every trade opened during the walk (run_id / run_label / config_hash) so
    overlapping replay windows stay isolated and A/B-comparable.
    """
    rows = await state.feature_rows_since(session, symbol, tf, since, until=until)
    report = ReplayReport(symbol=symbol, timeframe=tf)
    if not rows:
        report.notes.append("no feature rows in window")
        return report

    token = current_run.set(run)
    try:
        return await _walk_replay(session, client, symbol=symbol, tf=tf, rows=rows, report=report)
    finally:
        current_run.reset(token)


async def _walk_replay(
    session: AsyncSession,
    client: LlmClient,
    *,
    symbol: str,
    tf: str,
    rows: list,
    report: ReplayReport,
) -> ReplayReport:
    run = current_run.get()
    run_id = run.run_id if run else None
    bars_since_plan = settings.plan_refresh_bars  # force a plan on the first bar
    active_sticky: str | None = None
    observe_tf = settings.observe_timeframe
    base_dt = timeframe_to_timedelta(tf)
    obs_dt = timeframe_to_timedelta(observe_tf) if observe_tf != tf else base_dt
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
        if (
            open_before
            and settings.observe_enabled
            and prev is not None
            and observe_tf != tf
        ):
            fine_candles = await state.candles_between(
                session, symbol, observe_tf, now - obs_dt, now + base_dt - obs_dt
            )

        created, bars_since_plan, setup_count = await _ensure_plan(
            session, client, symbol=symbol, tf=tf, now=now, bars_since_plan=bars_since_plan,
            run_id=run_id,
        )
        if created:
            report.plans_created += 1
            if setup_count > 0:
                report.plans_with_setups += 1
        tick = await evaluate_now(
            session, client, symbol=symbol, tf=tf, feature_row=row, prev_row=prev,
            candle_closed=True, now=now, fine_candles=fine_candles, run_id=run_id,
        )
        _accumulate(report, tick)
        active_sticky = _record_notes(report, tick, active_sticky)
        report.bars += 1

        # Re-plan on close: the thesis that anchored the just-closed trade is stale, so
        # discard the active plan and force a fresh one on the next bar.
        if (
            settings.replan_on_close
            and tick.closed > 0
            and await state.supersede_active_plan(session, symbol, run_id=run_id)
        ):
            bars_since_plan = settings.plan_refresh_bars

    await session.commit()
    report.finalise()
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

    await _ensure_plan(
        session, client, symbol=symbol, tf=tf, now=now, bars_since_plan=settings.plan_refresh_bars
    )
    tick = await evaluate_now(
        session, client, symbol=symbol, tf=tf, feature_row=latest, prev_row=prev,
        candle_closed=True, now=now,
    )
    if settings.replan_on_close and tick.closed > 0:
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
