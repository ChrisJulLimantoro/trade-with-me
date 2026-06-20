"""Deterministic plan/setup invalidation.

Invalidation is evaluated by CODE, never by asking the LLM every tick. Severity
levels follow the architecture doc:
- warning: monitor only (no action)
- soft:    pause new entries
- hard:    kill the plan, cancel its setups, request a replan

``on_close`` rules only fire on a closed candle so a brief intrabar wick can't
trip a hard invalidation.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ats.config import settings
from ats.db.models import Plan, Setup
from ats.engine.closing import close_trade, open_trades_for
from ats.engine.quantities import _f
from ats.engine.reports import TickReport
from ats.engine.rule_engine import eval_rule
from ats.execution.executor import update_trade_state
from ats.execution.reconcile import ExitResult, TradeState, breakeven_stop, net_of_costs
from ats.llm.client import LlmClient

_SEVERITY_RANK = {"warning": 0, "soft": 1, "hard": 2}


def evaluate_invalidation(
    rules: list[dict[str, Any]],
    features: dict[str, Any],
    prev: dict[str, Any] | None = None,
    *,
    candle_closed: bool,
) -> str | None:
    """Return the highest-severity triggered level, or None.

    A rule with ``on_close=True`` is skipped unless ``candle_closed`` is True.
    """
    triggered: list[str] = []
    for rule in rules or []:
        if rule.get("on_close", True) and not candle_closed:
            continue
        if eval_rule(rule, features, prev):
            triggered.append(rule.get("severity", "warning"))
    if not triggered:
        return None
    return max(triggered, key=lambda s: _SEVERITY_RANK.get(s, 0))


async def handle_invalidation(
    session: AsyncSession,
    client: LlmClient | None,
    plan: Plan,
    setups: list[Setup],
    feature_row: dict[str, Any],
    prev_row: dict[str, Any] | None,
    candle_closed: bool,
    report: TickReport,
    *,
    run_id: str | None = None,
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
            for trade in await open_trades_for(session, s.symbol, run_id=run_id):
                if trade.setup_id == s.setup_id:
                    direction = trade.direction
                    entry = _f(trade.entry_price)
                    st = TradeState.from_metadata(trade.trade_metadata)
                    # A stopped-out trade can never fill worse than its (working) stop: if a
                    # price-level invalidation resolves beyond the stop, reality would have
                    # filled the stop first. Bound the exit so invalidation can't print a loss
                    # larger than a stop-out at this level.
                    ws = st.working_stop if st.working_stop is not None else _f(trade.stop_loss)
                    price = feature_row["price"]
                    price = max(price, ws) if direction == "long" else min(price, ws)
                    raw = (price - entry) / entry if entry else 0.0
                    leg = raw if direction == "long" else -raw
                    cost_bps = settings.risk.fee_bps + settings.risk.slippage_bps
                    total = st.realized_pnl_pct + net_of_costs(
                        st.remaining_frac, leg, cost_bps
                    )
                    raw_atr = feature_row.get("atr_14")
                    await close_trade(
                        session,
                        client,
                        trade,
                        ExitResult(price, feature_row["open_time"], "invalidation", total),
                        report,
                        atr_at_close=float(raw_atr) if raw_atr is not None else None,
                    )
        elif sev == "soft":
            paused = True
        elif sev == "warning":
            # Thesis weakening: protect the open trade by moving its stop to breakeven
            # and pause new entries this bar.
            paused = True
            for trade in await open_trades_for(session, s.symbol, run_id=run_id):
                if trade.setup_id != s.setup_id or trade.status != "open":
                    continue
                st = TradeState.from_metadata(trade.trade_metadata)
                if not st.breakeven:
                    cost_bps = settings.risk.fee_bps + settings.risk.slippage_bps
                    st.working_stop = breakeven_stop(
                        trade.direction, _f(trade.entry_price), cost_bps
                    )
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
