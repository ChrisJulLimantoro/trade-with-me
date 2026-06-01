"""Detector — the glue between the rule engine and the trade lifecycle.

For a given "now" (a feature row), against the active plan it:
1. evaluates invalidation rules → may kill the plan and close open trades,
2. for each armed setup, runs the rule engine; on detection asks confirm_setup,
   applies risk, and opens a paper trade.

It also reconciles open trades against the current bar (SL / TP / expiry). LLM calls
(confirm_setup) only happen on a detection, keeping cost proportional to opportunities.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ats.config import settings
from ats.db.models import LlmCall, PaperTrade, Plan, Setup
from ats.engine import state
from ats.engine.invalidation import evaluate_invalidation
from ats.engine.rule_engine import evaluate_setup
from ats.execution.executor import (
    close_paper_trade,
    open_paper_trade,
    record_partial_exit,
    update_trade_state,
)
from ats.execution.reconcile import ExitResult, TradeState, step_trade
from ats import trace
from ats.llm.client import LlmClient
from ats.logging import get_logger
from ats.risk.manager import assess, regime_allows

log = get_logger(__name__)


@dataclass
class TickReport:
    now: datetime | None = None
    plan_id: uuid.UUID | None = None
    detections: int = 0
    opened: int = 0
    closed: int = 0
    confirm_calls: int = 0
    risk_rejected: int = 0
    plan_invalidated: bool = False
    paused: bool = False
    notes: list[str] = field(default_factory=list)


def _f(v: Any) -> float:
    return float(v) if isinstance(v, Decimal) else float(v)


def _setup_dict(s: Setup) -> dict[str, Any]:
    return {
        "setup_id": s.setup_id,
        "plan_id": s.plan_id,
        "symbol": s.symbol,
        "direction": s.direction,
        "status": s.status,
        "entry_zone_low": _f(s.entry_zone_low),
        "entry_zone_high": _f(s.entry_zone_high),
        "stop_loss": _f(s.stop_loss),
        "take_profit": [float(x) for x in s.take_profit],
        "size_pct": _f(s.size_pct),
        "hard_rules": s.hard_rules,
        "soft_rules": s.soft_rules,
        "invalidation_rules": s.invalidation_rules,
        "expires_at": s.expires_at,
    }


async def _open_trades_for(session: AsyncSession, symbol: str) -> list[PaperTrade]:
    stmt = select(PaperTrade).where(PaperTrade.symbol == symbol, PaperTrade.status == "open")
    return list((await session.execute(stmt)).scalars().all())


async def _reconcile_open_trades(
    session: AsyncSession,
    symbol: str,
    candle: dict[str, Any],
    report: TickReport,
    *,
    atr: float | None = None,
) -> None:
    """Advance each open trade by this bar: scale out, trail, or close on stop/target/expiry."""
    for trade in await _open_trades_for(session, symbol):
        if trade.entry_time >= candle["open_time"]:
            continue  # entered this bar or later — can't exit on its own entry bar
        raw_expiry = (trade.trade_metadata or {}).get("expires_at")
        expires_at = (
            datetime.fromisoformat(raw_expiry)
            if isinstance(raw_expiry, str)
            else candle["open_time"]
        )
        trade_d = {
            "direction": trade.direction,
            "entry_price": _f(trade.entry_price),
            "stop_loss": _f(trade.stop_loss),
            "take_profit": [float(x) for x in trade.take_profit],
        }
        state = TradeState.from_metadata(trade.trade_metadata)
        step = step_trade(
            trade_d,
            candle,
            state,
            expires_at=expires_at,
            scale_out_frac=settings.scale_out_frac,
            trail_atr_mult=settings.trail_atr_mult,
            atr=atr,
        )
        if step.closed:
            await close_paper_trade(
                session,
                trade.trade_id,
                step.exit_result,
                equity_usd=settings.paper_equity_usd,
                size_pct=_f(trade.size_pct),
            )
            report.closed += 1
        elif step.partial is not None:
            await record_partial_exit(
                session,
                trade.trade_id,
                step.partial,
                step.state,
                equity_usd=settings.paper_equity_usd,
                size_pct=_f(trade.size_pct),
            )
        elif step.state.working_stop != state.working_stop:
            await update_trade_state(session, trade.trade_id, step.state)


async def _handle_invalidation(
    session: AsyncSession,
    plan: Plan,
    setups: list[Setup],
    feature_row: dict[str, Any],
    prev_row: dict[str, Any] | None,
    candle_closed: bool,
    report: TickReport,
) -> bool:
    """Evaluate invalidation across the plan's setups. Returns True if plan was killed."""
    paused = False
    hard = False
    for s in setups:
        sev = evaluate_invalidation(
            s.invalidation_rules, feature_row, prev_row, candle_closed=candle_closed
        )
        if sev == "hard":
            hard = True
            s.status = "invalidated"
            # close any open trade for this setup at the current close, banking partials
            for trade in await _open_trades_for(session, s.symbol):
                if trade.setup_id == s.setup_id:
                    price = feature_row["price"]
                    direction = trade.direction
                    entry = _f(trade.entry_price)
                    raw = (price - entry) / entry if entry else 0.0
                    leg = raw if direction == "long" else -raw
                    st = TradeState.from_metadata(trade.trade_metadata)
                    total = st.realized_pnl_pct + st.remaining_frac * leg
                    await close_paper_trade(
                        session,
                        trade.trade_id,
                        ExitResult(price, feature_row["open_time"], "invalidation", total),
                        equity_usd=settings.paper_equity_usd,
                        size_pct=_f(trade.size_pct),
                    )
                    report.closed += 1
        elif sev == "soft":
            paused = True
        elif sev == "warning":
            # Thesis weakening: protect the open trade by moving its stop to breakeven
            # and pause new entries this bar.
            paused = True
            for trade in await _open_trades_for(session, s.symbol):
                if trade.setup_id != s.setup_id or trade.status != "open":
                    continue
                st = TradeState.from_metadata(trade.trade_metadata)
                if not st.breakeven:
                    st.working_stop = _f(trade.entry_price)
                    st.breakeven = True
                    await update_trade_state(session, trade.trade_id, st)
                    report.notes.append(
                        f"warning invalidation on setup {s.setup_id}: stop→breakeven"
                    )

    if hard:
        plan.status = "invalidated"
        report.plan_invalidated = True
        await session.flush()
        return True
    if paused:
        report.notes.append("soft invalidation: new entries paused")
        report.paused = True
    return False


async def _confirm_and_execute(
    session: AsyncSession,
    client: LlmClient,
    plan: Plan,
    setup: Setup,
    setup_d: dict[str, Any],
    ev: Any,
    feature_row: dict[str, Any],
    now: datetime,
    open_positions: list[dict[str, Any]],
    report: TickReport,
) -> None:
    confirm_env = {
        "now": now,
        "symbol": setup.symbol,
        "setup": {k: v for k, v in setup_d.items() if k not in ("expires_at",)},
        "rule_eval": {
            "price": ev.price,
            "hard_ok": ev.hard_ok,
            "soft_score": ev.soft_score,
            "failed_hard": ev.failed_hard,
        },
        "features_now": feature_row,
        "plan_bias": plan.market_bias,
    }
    confirm, llm = await client.confirm_setup(confirm_env, symbol=setup.symbol)
    report.confirm_calls += 1
    trace.confirm(
        now=now,
        plan_id=plan.plan_id,
        setup_id=setup.setup_id,
        ev=ev,
        action=(confirm.action if confirm else "PARSE_FAIL"),
        reason=(confirm.reason if confirm else None),
        size_multiplier=(confirm.size_multiplier if confirm else 1.0),
    )
    session.add(
        LlmCall(
            call_id=uuid.uuid4(),
            kind="confirm_setup",
            model=llm.model,
            mock=llm.mock,
            symbol=setup.symbol,
            plan_id=plan.plan_id,
            setup_id=setup.setup_id,
            input_tokens=llm.input_tokens,
            output_tokens=llm.output_tokens,
            cost_usd=llm.cost_usd,
            latency_ms=llm.latency_ms,
            parse_ok=llm.parse_ok,
            response=llm.raw,
        )
    )

    if not llm.parse_ok or confirm is None or confirm.action in ("WAIT", "REJECT"):
        if confirm is not None and confirm.action == "REJECT":
            setup.status = "rejected"
        trace.outcome(f"SKIPPED: {confirm.action if confirm else 'parse_fail'}")
        report.notes.append(
            f"setup {setup.setup_id} not executed ({confirm.action if confirm else 'parse_fail'})"
        )
        return

    multiplier = confirm.size_multiplier if confirm.action == "REDUCE_SIZE" else 1.0
    decision = assess(
        setup_d,
        price=ev.price,
        open_positions=open_positions,
        max_position_pct=settings.max_position_pct,
        min_rr=settings.min_rr,
        size_multiplier=multiplier,
    )
    if not decision.approved:
        report.risk_rejected += 1
        trace.outcome(f"SKIPPED: risk-rejected — {'; '.join(decision.reasons)}")
        report.notes.append(f"setup {setup.setup_id} risk-rejected: {decision.reasons}")
        return

    trade_id = await open_paper_trade(
        session,
        {**setup_d, "trade_metadata": None},
        entry_price=ev.price,
        entry_time=now,
        size_pct=decision.size_pct,
        reasons=[f"confirm:{confirm.action}", *decision.reasons],
    )
    # stash setup expiry so reconciliation can apply it without a join
    trade = await session.get(PaperTrade, trade_id)
    if trade is not None:
        trade.trade_metadata = {"expires_at": setup.expires_at.isoformat()}
    setup.status = "realized"
    setup.detected_at = now
    open_positions.append({"symbol": setup.symbol})
    report.opened += 1
    trace.outcome(f"EXECUTED — {'; '.join(decision.reasons)}")
    await session.flush()


async def evaluate_now(
    session: AsyncSession,
    client: LlmClient,
    *,
    symbol: str,
    feature_row: dict[str, Any],
    prev_row: dict[str, Any] | None,
    candle_closed: bool = True,
    now: datetime | None = None,
) -> TickReport:
    """Run one full evaluation of the active plan against the current feature row."""
    now = now or feature_row["open_time"]
    report = TickReport(now=now)

    candle = {
        "open_time": feature_row["open_time"],
        "high": feature_row["high"],
        "low": feature_row["low"],
        "close": feature_row["close"],
    }
    atr = feature_row.get("atr_14")
    await _reconcile_open_trades(
        session, symbol, candle, report, atr=_f(atr) if atr is not None else None
    )

    plan = await state.active_plan(session, symbol)
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
    killed = await _handle_invalidation(
        session, plan, setups, feature_row, prev_row, candle_closed, report
    )
    if killed or report.paused:
        return report

    open_positions = await state.open_positions(session, symbol=symbol)
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
        setup_d = _setup_dict(setup)
        ev = evaluate_setup(setup_d, feature_row, prev_row, soft_threshold=settings.soft_threshold)
        if not ev.detected:
            continue
        report.detections += 1
        if settings.regime_filter and not regime_allows(plan.regime_cell, setup.direction):
            trace.outcome(f"SKIPPED: regime {plan.regime_cell} disallows {setup.direction}")
            report.notes.append(
                f"setup {setup.setup_id} regime-filtered ({plan.regime_cell}/{setup.direction})"
            )
            continue
        await _confirm_and_execute(
            session, client, plan, setup, setup_d, ev, feature_row, now, open_positions, report
        )

    await session.flush()
    return report
