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
from ats.synthesis.sl_tp import adaptive_stop_mult


def _f_confidence(s: Setup) -> float | None:
    """Setup's post-adjudication confidence, or ``None`` when not persisted.

    Stored on ``setup_metadata["confidence"]`` by ``create_plan._persist``. ``None`` covers
    both legacy pre-field setups and vetoed-signal plans (no valid confidence to size on).
    """
    raw = (s.setup_metadata or {}).get("confidence")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def conviction_size_multiplier(
    confidence: float | None,
    *,
    enabled: bool,
    size_min: float,
    size_max: float,
    conf_floor: float,
    fallback_floor: float,
) -> float:
    """Linear conviction→risk multiplier in ``[size_min, size_max]`` (1.0 = OFF).

    Linear on the confidence band ``[conf_floor, 1.0]``: a marginal setup sitting at the
    admission floor (``signal_min_confidence``) receives ``size_min`` (starved), a peak-
    conviction setup at confidence=1.0 receives ``size_max`` (concentrated). ``conf_floor``
    defaults to ``fallback_floor`` (the profile's ``signal_min_confidence``) when 0 — so the
    profile inherits its own floor without a duplicate knob. ``confidence=None`` (legacy
    or vetoed-signal setups with no confidence stored) → 1.0 (no-op); never gate-kill on
    missing data. Pure & deterministic; the same arguments give the byte-identical result.
    """
    if not enabled or confidence is None:
        return 1.0
    floor = conf_floor if conf_floor > 0.0 else fallback_floor
    if floor >= 1.0:
        return size_max
    span = 1.0 - floor
    c = min(1.0, max(0.0, confidence))
    frac = (c - floor) / span  # ∈ [0, 1] after clamp; below-floor setups clamp to 0
    return size_min + (size_max - size_min) * max(0.0, frac)


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
        # Post-adjudication confidence persisted at _persist (create_plan.py) so the entry
        # path can modulate risk by conviction (see RiskConfig.conviction_sizing_*). ``None``
        # for setups persisted before this field existed or when the signal was vetoed.
        "confidence": _f_confidence(s),
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
    equity_usd: float | None = None,
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
    pr_atr_raw = feature_row.get("pr_atr")
    pr_atr = float(pr_atr_raw) if pr_atr_raw is not None else None
    # Volatility-scaled sizing: bias risk toward locally expanded volatility (high pr_atr — trend
    # legs that pay off) and away from compressed chop (low pr_atr — the 59%-win bleed). Causal:
    # pr_atr is a trailing percentile. Applied here (post-admission) so it never changes which
    # setups trade — only how much risk each gets.
    size_mult = 1.0
    if settings.risk.vol_sizing_enabled and pr_atr is not None:
        p = min(1.0, max(0.0, pr_atr))
        size_mult = settings.risk.vol_size_min + (
            settings.risk.vol_size_max - settings.risk.vol_size_min
        ) * p
    # Conviction-based sizing (NEW mechanism, default OFF; sibling to vol-sizing): scale the
    # per-trade risk budget by the SETUP's confidence — concentrate capital on high-conviction
    # entries, starve marginal-floor entries. Sibling, not override: the two multipliers compose
    # multiplicatively (so vol-sizing-ON × conviction-ON with both ~1.4 lifts a peak, peak-vol
    # entry ~2.0 — the assess() cap). ``confidence`` is read off the setup's persisted metadata
    # (written at create_plan._persist from the Signal) so the *post-adjudication* confidence is
    # what drives sizing. None → setups persisted before this field existed or vetoed-signal
    # plans → conviction_size_multiplier returns 1.0 (no-op; never gate-kill on missing data).
    conv_mult = conviction_size_multiplier(
        setup_d.get("confidence"),
        enabled=settings.risk.conviction_sizing_enabled,
        size_min=settings.risk.conviction_size_min,
        size_max=settings.risk.conviction_size_max,
        conf_floor=settings.risk.conviction_size_conf_floor,
        fallback_floor=settings.plan.signal_min_confidence,
    )
    size_mult *= conv_mult
    # Noise-stop admission floor must TRACK the adaptive stop width (built in the synthesizer from
    # the same pr_atr) — otherwise a widened chop stop clears a fixed floor and the chop trades the
    # widening was meant to protect get ADMITTED instead (the iter-11 confound). With the floor
    # tracking the width, admission keeps the same ATR-drift rejection behavior as the fixed stop.
    atr_pct_raw = feature_row.get("atr_pct")
    noise_floor = adaptive_stop_mult(
        float(atr_pct_raw) if atr_pct_raw is not None else None,
        settings.risk.min_stop_atr_mult,
        settings.risk.stop_atr_wide,
        settings.risk.stop_vol_lo,
        settings.risk.stop_vol_hi,
        settings.risk.adaptive_stop_enabled,
    )
    decision = assess(
        setup_d,
        price=entry_price,
        open_positions=open_positions,
        equity_usd=equity_usd if equity_usd is not None else settings.risk.paper_equity_usd,
        risk_per_trade_pct=settings.risk.risk_per_trade_pct,
        max_leverage=settings.risk.max_leverage,
        min_rr=settings.risk.min_rr,
        max_margin_pct_per_trade=settings.risk.max_margin_pct_per_trade,
        max_total_margin_pct=settings.risk.max_total_margin_pct,
        max_portfolio_risk_pct=settings.risk.max_portfolio_risk_pct,
        size_multiplier=size_mult,
        atr=float(raw_atr) if raw_atr is not None else None,
        min_stop_atr_mult=noise_floor,
    )
    if not decision.approved:
        report.risk_rejected += 1
        trace.outcome(f"SKIPPED: risk-rejected — {'; '.join(decision.reasons)}")
        report.notes.append(f"setup {setup.setup_id} risk-rejected: {decision.reasons}")
        return

    effective_equity = equity_usd if equity_usd is not None else settings.risk.paper_equity_usd
    trace.outcome(f"sizing on equity=${effective_equity:.2f}")
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
    if trade_id is None:
        # Live execution on and the testnet entry order was rejected/too small: do not open
        # an internal trade. open_paper_trade already traced the reason.
        report.notes.append(f"setup {setup.setup_id} live entry order not filled")
        return
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
