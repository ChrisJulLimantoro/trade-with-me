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

from ats.llm.schemas import (
    ConfirmOutput,
    InvalidationRule,
    PlanOutput,
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


def canned_plan(envelope: dict[str, Any]) -> PlanOutput:
    """Build a deterministic plan from the market snapshot."""
    bias = _bias(envelope)
    price = _price(envelope)
    max_size = float((envelope.get("risk_limits") or {}).get("max_position_pct", 0.10))

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
        size_pct=min(0.05, max_size),
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
