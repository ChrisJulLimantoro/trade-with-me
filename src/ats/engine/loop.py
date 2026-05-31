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
from ats.engine.detector import TickReport, evaluate_now
from ats.llm.client import LlmClient
from ats.logging import get_logger
from ats.planning.create_plan import create_plan

log = get_logger(__name__)


@dataclass
class ReplayReport:
    symbol: str
    timeframe: str
    bars: int = 0
    plans_created: int = 0
    detections: int = 0
    opened: int = 0
    closed: int = 0
    invalidations: int = 0
    confirm_calls: int = 0
    notes: list[str] = field(default_factory=list)


def _accumulate(report: ReplayReport, tick: TickReport) -> None:
    report.detections += tick.detections
    report.opened += tick.opened
    report.closed += tick.closed
    report.confirm_calls += tick.confirm_calls
    if tick.plan_invalidated:
        report.invalidations += 1


async def _ensure_plan(
    session: AsyncSession,
    client: LlmClient,
    *,
    symbol: str,
    tf: str,
    now: datetime,
    bars_since_plan: int,
) -> tuple[bool, int]:
    """Refresh the plan if there is none, it expired, or the refresh cadence elapsed."""
    plan = await state.active_plan(session, symbol)
    stale = plan is None or now >= plan.expires_at or bars_since_plan >= settings.plan_refresh_bars
    if stale:
        await create_plan(session, client, symbol=symbol, tf=tf, as_of=now)
        return True, 0
    return False, bars_since_plan + 1


async def run_replay(
    session: AsyncSession,
    client: LlmClient,
    *,
    symbol: str,
    tf: str,
    since: datetime,
    until: datetime | None = None,
) -> ReplayReport:
    """Walk historical feature rows, running the full loop on each as if live."""
    rows = await state.feature_rows_since(session, symbol, tf, since, until=until)
    report = ReplayReport(symbol=symbol, timeframe=tf)
    if not rows:
        report.notes.append("no feature rows in window")
        return report

    bars_since_plan = settings.plan_refresh_bars  # force a plan on the first bar
    for i, row in enumerate(rows):
        now = row["open_time"]
        created, bars_since_plan = await _ensure_plan(
            session, client, symbol=symbol, tf=tf, now=now, bars_since_plan=bars_since_plan
        )
        if created:
            report.plans_created += 1
        prev = rows[i - 1] if i > 0 else None
        tick = await evaluate_now(
            session, client, symbol=symbol, feature_row=row, prev_row=prev,
            candle_closed=True, now=now,
        )
        _accumulate(report, tick)
        report.bars += 1

    await session.commit()
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
        session, client, symbol=symbol, feature_row=latest, prev_row=prev,
        candle_closed=True, now=now,
    )
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
        )
        if once:
            return
        await asyncio.sleep(interval)
