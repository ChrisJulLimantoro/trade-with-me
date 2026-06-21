"""Entry path: size and open a detected setup deterministically.

The per-setup LLM confirm was retired in Part 2 — the bounded plan-time adjudication
already vetoed-or-sized the setup before persistence. A detection that clears the
deterministic gates (rule engine + regime + entry-confirmation) goes straight to the risk
manager and opens at full size, with no LLM call on the entry path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ats import trace
from ats.config import settings
from ats.db.models import PaperTrade, Plan, Setup
from ats.engine.exits.policy import exit_policy_for_regime, exit_policy_metadata
from ats.engine.quantities import _f
from ats.engine.reports import TickReport
from ats.engine.timeframes import timeframe_to_timedelta
from ats.execution.executor import open_paper_trade
from ats.risk.manager import assess


def setup_dict(s: Setup) -> dict[str, Any]:
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


async def execute_setup(
    session: AsyncSession,
    plan: Plan,
    setup: Setup,
    setup_d: dict[str, Any],
    entry_price: float,
    feature_row: dict[str, Any],
    now: datetime,
    open_positions: list[dict[str, Any]],
    report: TickReport,
    *,
    tf: str,
) -> None:
    """Size and open a detected setup deterministically at ``entry_price``.

    ``entry_price`` is the fill the caller resolved: a resting limit price (wick_limit, filled
    on a later bar) or the decision bar's close (close mode). The per-setup LLM confirm is
    retired (Part 2): the bounded plan-time adjudication already vetoed-or-sized this setup
    before it was persisted, so a detection that clears the deterministic gates (rule engine +
    regime + entry-confirmation) goes straight to the risk manager and opens at full size. No
    LLM call happens on the entry path.
    """
    raw_atr = feature_row.get("atr_14")
    decision = assess(
        setup_d,
        price=entry_price,
        open_positions=open_positions,
        equity_usd=settings.risk.paper_equity_usd,
        risk_per_trade_pct=settings.risk.risk_per_trade_pct,
        max_leverage=settings.risk.max_leverage,
        min_rr=settings.risk.min_rr,
        max_margin_pct_per_trade=settings.risk.max_margin_pct_per_trade,
        max_total_margin_pct=settings.risk.max_total_margin_pct,
        max_portfolio_risk_pct=settings.risk.max_portfolio_risk_pct,
        size_multiplier=1.0,
        atr=float(raw_atr) if raw_atr is not None else None,
        min_stop_atr_mult=settings.risk.min_stop_atr_mult,
    )
    if not decision.approved:
        report.risk_rejected += 1
        trace.outcome(f"SKIPPED: risk-rejected — {'; '.join(decision.reasons)}")
        report.notes.append(f"setup {setup.setup_id} risk-rejected: {decision.reasons}")
        return

    policy = exit_policy_for_regime(plan.regime_cell)
    trade_id = await open_paper_trade(
        session,
        {**setup_d, "trade_metadata": None},
        entry_price=entry_price,
        entry_time=now,
        size_pct=decision.size_pct,
        reasons=["detected", *decision.reasons],
        leverage=decision.leverage,
        margin_usd=decision.margin_usd,
        notional_usd=decision.notional_usd,
        liq_price=decision.liq_price,
        risk_usd=decision.risk_usd,
    )
    # Time-stop the OPEN trade from its own entry bar, not the setup's entry-window
    # expiry: a trade that triggers late in a plan's life still gets a full hold budget,
    # so a healthy, still-running position is no longer cut short by the plan clock.
    trade = await session.get(PaperTrade, trade_id)
    if trade is not None:
        md = exit_policy_metadata(policy)
        md.update(
            entry_zone_low=_f(setup.entry_zone_low),
            entry_zone_high=_f(setup.entry_zone_high),
        )
        if settings.exits.max_hold_bars > 0:
            hold = timeframe_to_timedelta(tf) * settings.exits.max_hold_bars
            md.update(
                expires_at=(now + hold).isoformat(),
                # Window length each thesis-review extension grants (see advance_trade).
                hold_window_seconds=hold.total_seconds(),
            )
        trade.trade_metadata = md
    setup.status = "realized"
    setup.detected_at = now
    open_positions.append(
        {
            "symbol": setup.symbol,
            "margin_usd": decision.margin_usd,
            "notional_usd": decision.notional_usd,
            "risk_usd": decision.risk_usd,
            "leverage": decision.leverage,
            "size_pct": decision.size_pct,
        }
    )
    report.opened += 1
    trace.outcome(f"EXECUTED — {'; '.join(decision.reasons)}")
    trace.outcome(
        "EXIT POLICY "
        f"mode={policy.exit_mode} be_after_tp1={policy.breakeven_requires_tp1} "
        f"trail_atr={policy.trail_atr_mult:g}"
    )
    await session.flush()
