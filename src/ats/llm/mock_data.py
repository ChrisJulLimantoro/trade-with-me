"""Deterministic mock LLM outputs.

The mock turns a structured envelope into a valid ``PlanOutput`` / ``ConfirmOutput``
WITHOUT any network call, so the entire pipeline (rule engine → confirm → risk →
paper executor → reconciliation) and all tests run with no API key and zero token
spend. Output is a pure function of the envelope, so replays are reproducible.

The plan it builds is intentionally simple but realistic: it derives a bias from the
regime/EMA structure, then anchors an entry zone around the current price with a
symmetric stop and two take-profits, plus a few rules over standard features.
"""

from __future__ import annotations

from typing import Any

from ats.config import settings
from ats.llm.schemas import (
    ConfirmOutput,
    InvalidationRule,
    ObservationOutput,
    PlanOutput,
    ReflectionOutput,
    Rule,
    SetupOutput,
)


def _price(envelope: dict[str, Any]) -> float | None:
    feats = envelope.get("features") or {}
    for key in ("close", "price"):
        v = feats.get(key)
        if v is not None:
            return float(v)
    return None


def _bias(envelope: dict[str, Any]) -> str:
    """Bias from regime trend, falling back to EMA structure."""
    regime = envelope.get("regime") or {}
    trend = regime.get("trend")
    if trend == "bull":
        return "bullish"
    if trend == "bear":
        return "bearish"
    feats = envelope.get("features") or {}
    ema_50, ema_200 = feats.get("ema_50"), feats.get("ema_200")
    if ema_50 is not None and ema_200 is not None:
        if float(ema_50) > float(ema_200):
            return "bullish"
        if float(ema_50) < float(ema_200):
            return "bearish"
    return "neutral"


def _preferred_bias(envelope: dict[str, Any]) -> str | None:
    """Map the deterministic preferred_direction hint (#4) onto a mock bias, if present."""
    pref = (envelope.get("risk_limits") or {}).get("preferred_direction")
    if pref == "long":
        return "bullish"
    if pref == "short":
        return "bearish"
    return None


def canned_plan(envelope: dict[str, Any]) -> PlanOutput:
    """Build a deterministic plan from the market snapshot."""
    # Honor the deterministic preferred-direction hint when present; otherwise fall back to the
    # regime/EMA bias. Flag OFF → no hint in the envelope → unchanged baseline behavior.
    bias = _preferred_bias(envelope) or _bias(envelope)
    price = _price(envelope)
    # Nominal only — the risk manager sizes by risk/leverage and ignores this.
    nominal_size = 0.05

    if bias == "neutral" or price is None:
        # No conviction (or no price) → a plan with no actionable setups.
        return PlanOutput(market_bias=bias, rationale="No directional edge; standing aside.")

    if bias == "bullish":
        entry_zone = [round(price * 0.995, 8), round(price * 1.005, 8)]
        stop_loss = round(price * 0.985, 8)
        take_profit = [round(price * 1.02, 8), round(price * 1.04, 8)]
        hard_rules = [Rule(left="rsi_14", operator="<", right=72)]
        soft_rules = [
            Rule(left="rsi_14", operator=">", right=48, weight=0.6),
            Rule(left="macd_hist", operator=">", right=0, weight=0.4),
        ]
        invalidation_rules = [
            InvalidationRule(severity="hard", left="price", operator="<", right=stop_loss),
            InvalidationRule(
                severity="soft", left="rsi_14", operator="<", right=40, on_close=False
            ),
        ]
        direction = "long"
    else:  # bearish
        entry_zone = [round(price * 0.995, 8), round(price * 1.005, 8)]
        stop_loss = round(price * 1.015, 8)
        take_profit = [round(price * 0.98, 8), round(price * 0.96, 8)]
        hard_rules = [Rule(left="rsi_14", operator=">", right=28)]
        soft_rules = [
            Rule(left="rsi_14", operator="<", right=52, weight=0.6),
            Rule(left="macd_hist", operator="<", right=0, weight=0.4),
        ]
        invalidation_rules = [
            InvalidationRule(severity="hard", left="price", operator=">", right=stop_loss),
            InvalidationRule(
                severity="soft", left="rsi_14", operator=">", right=60, on_close=False
            ),
        ]
        direction = "short"

    setup = SetupOutput(
        direction=direction,
        entry_zone=entry_zone,
        take_profit=take_profit,
        stop_loss=stop_loss,
        size_pct=nominal_size,
        hard_rules=hard_rules,
        soft_rules=soft_rules,
        invalidation_rules=invalidation_rules,
    )
    return PlanOutput(
        market_bias=bias,
        rationale=f"Mock {bias} plan anchored at {price:.2f} from regime/EMA structure.",
        allowed_setups=[setup],
    )


def canned_confirm(envelope: dict[str, Any]) -> ConfirmOutput:
    """Confirm based on the deterministic soft-score already computed by the engine."""
    rule_eval = envelope.get("rule_eval") or {}
    soft_score = float(rule_eval.get("soft_score", 1.0))
    if soft_score >= 0.7:
        return ConfirmOutput(action="CONFIRM", reason=f"Strong soft score {soft_score:.2f}.")
    if soft_score >= 0.5:
        return ConfirmOutput(
            action="REDUCE_SIZE",
            reason=f"Marginal soft score {soft_score:.2f}.",
            size_multiplier=0.5,
        )
    return ConfirmOutput(action="WAIT", reason=f"Weak soft score {soft_score:.2f}.")


def canned_observation(envelope: dict[str, Any]) -> ObservationOutput:
    """Deterministic exit-management decision from unrealized P&L + momentum.

    Mirrors a sensible discretionary trader: protect a healthy gain when momentum fades,
    let a strong winner run, and bail when momentum has clearly flipped against the trade.
    Pure function of the envelope, so replays stay reproducible.
    """
    direction = envelope.get("direction", "long")
    unrealized = float(
        envelope.get("unrealized_margin_pct", envelope.get("unrealized_pct", 0.0))
    )
    price = float(envelope.get("current_price", 0.0))
    feats = envelope.get("features_now") or {}
    macd_hist = feats.get("macd_hist")
    mh = float(macd_hist) if macd_hist is not None else 0.0
    # Momentum "with the trade" is positive macd_hist for longs, negative for shorts.
    momentum_with = mh if direction == "long" else -mh
    progress = envelope.get("progress") or {}
    observer_context = envelope.get("observer_context") or {}
    thesis_health = observer_context.get("thesis_health") or {}
    excursion = observer_context.get("excursion") or {}
    pressure = str(observer_context.get("recommended_pressure") or "hold")
    hold = envelope.get("hold") or {}
    hold_progress = float(
        hold.get("current_window_progress")
        or hold.get("hold_progress")
        or 0.0
    )
    max_favorable = float(progress.get("max_favorable_pnl_pct") or 0.0)
    stale_candidate = bool(progress.get("stale_candidate")) or (
        hold_progress >= settings.observe_stale_hold_progress
        and abs(unrealized) <= settings.observe_stale_unrealized_abs_pct
        and max_favorable <= settings.observe_stale_mfe_pct
    )
    current_r = excursion.get("current_r")
    mfe_r = excursion.get("mfe_r")
    failed_to_travel = (
        current_r is not None
        and mfe_r is not None
        and float(current_r) <= -0.5
        and float(mfe_r) < 0.25
    )

    if pressure == "exit" or thesis_health.get("status") == "broken":
        return ObservationOutput(
            action="EXIT_NOW",
            reason="Observer context marks thesis broken or exit pressure.",
            confidence=0.85,
        )
    if pressure == "cut_or_tighten" and (unrealized <= 0.0 or failed_to_travel):
        return ObservationOutput(
            action="EXIT_NOW",
            reason="Trade is red or failed to travel under cut-or-tighten pressure.",
            confidence=0.78,
        )

    # Clear reversal against an at-risk position → exit.
    if unrealized <= 0.0 and momentum_with < 0:
        return ObservationOutput(
            action="EXIT_NOW",
            reason="Momentum flipped against the trade while not in profit.",
            confidence=0.8,
        )
    # Opportunity-cost exit: the trade used most of its hold budget and never moved.
    if stale_candidate and momentum_with <= 0:
        return ObservationOutput(
            action="EXIT_NOW",
            reason="Trade is stale: most of the hold window is spent without favorable progress.",
            confidence=0.75,
        )
    # Healthy gain but momentum fading → lock in by tightening toward price.
    if unrealized >= 0.01 and momentum_with <= 0:
        # Pull the stop to roughly halfway between entry and current price.
        entry = float(envelope.get("entry_price", price))
        new_stop = round(entry + (price - entry) * 0.5, 8)
        return ObservationOutput(
            action="TIGHTEN_STOP",
            reason=f"Banking gains: unrealized {unrealized:+.2%}, momentum fading.",
            new_stop=new_stop,
        )
    # Strong continuation → let it run a bit further.
    if unrealized >= 0.015 and momentum_with > 0:
        tps = envelope.get("take_profit") or []
        if tps:
            final = float(tps[-1])
            extended = round(final * (1.01 if direction == "long" else 0.99), 8)
            return ObservationOutput(
                action="RAISE_TP",
                reason="Strong momentum with open profit; extend the final target.",
                new_tp=[extended],
            )
    return ObservationOutput(action="HOLD", reason="No clear edge to adjust this check.")


def canned_reflection(envelope: dict[str, Any]) -> ReflectionOutput:
    """Deterministic post-mortem from the closed trade's exit_reason + pnl.

    Accepts the trade fields either at the top level or nested under a "trade" key (the
    shape the post-mortem builder emits).
    """
    trade = envelope.get("trade") or envelope
    pnl = float(trade.get("pnl_pct", 0.0))
    reason = str(trade.get("exit_reason", ""))
    win = pnl > 0

    if reason in ("tp", "observe_exit") and win:
        category, quality = "clean_win", "good"
    elif reason == "sl" and not win:
        category, quality = "clean_loss", "poor"
    elif reason == "invalidation":
        category, quality = "regime_shift", "neutral"
    elif reason == "expiry":
        category, quality = "alignment_too_low", "neutral"
    else:
        category, quality = "other", "neutral"

    return ReflectionOutput(
        category=category,
        hypothesis=f"Trade closed via {reason or 'unknown'} at {pnl:+.2%}.",
        evidence=(
            f"direction={trade.get('direction')} "
            f"entry={trade.get('entry_price')} exit={trade.get('exit_price')}"
        ),
        proposed_adjustment="",
        confidence_in_lesson=0.4,
        decision_quality=quality,
    )
