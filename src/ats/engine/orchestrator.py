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
from ats.engine.entries import execute_setup, setup_dict
from ats.engine.exits.manager import reconcile_open_trades
from ats.engine.invalidation import handle_invalidation
from ats.engine.quantities import _f
from ats.engine.reports import ClosedTradeInfo, TickReport
from ats.engine.rule_engine import entry_confirmed, evaluate_setup
from ats.llm.client import LlmClient
from ats.logging import get_logger
from ats.risk.manager import regime_allows

__all__ = ["ClosedTradeInfo", "TickReport", "evaluate_now"]

log = get_logger(__name__)


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
    if open_positions:
        # One position per symbol: no new entry is possible, so don't spend an LLM
        # confirm call this bar. Existing trades are still reconciled above.
        report.notes.append("position open: skipping new entries")
        return report
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
        if not ev.detected:
            continue
        report.detections += 1
        # Enforce the direction gate the strategist was given. The planner persists the
        # exact allowed_directions it planned under (including any HTF-exhaustion counter-
        # trend relief) onto the plan, so honor that; fall back to the strict trend-only
        # gate for plans that predate the persisted field.
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
            continue
        if settings.plan.entry_confirmation_enabled:
            confirmed, reason = entry_confirmed(setup.direction, feature_row, prev_row)
            if not confirmed:
                trace.outcome(f"AWAITING confirmation: {reason}")
                report.notes.append(
                    f"setup {setup.setup_id} awaiting confirmation ({reason})"
                )
                continue
        opened_before = report.opened
        await execute_setup(
            session, plan, setup, setup_d, ev, feature_row, now, open_positions, report,
            tf=tf,
        )
        if report.opened > opened_before:
            break

    await session.flush()
    return report
