"""Bridge: synthesized ``Signal`` → existing ``PlanOutput`` / ``SetupOutput``.

The synthesized Signal and SetupOutput are nearly isomorphic — both carry direction,
entry_zone, stop_loss, take_profit, size_pct. Bridging here means the kept runtime
(detector, risk manager, exit machine, persistence) consumes the deterministic proposer
unchanged: it never learns the proposer stopped being the LLM.

Rule shape mirrors the proven mock-plan shape so the detector behaves identically:
- ``hard_rules`` stays empty — the ``entry_zone`` IS the executable price gate, and bare
  price-literal hard rules are the #1 cause of in-zone setups that never fill (see
  ``create_plan._stripped_hard_rules``).
- ``soft_rules`` restate the thesis direction over momentum features (drives the soft
  score), included only when the feature is present.
- ``invalidation_rules`` carry a structural, close-confirmed momentum-reversal guard.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ats.llm.schemas import InvalidationRule, MarketBias, PlanOutput, Rule, SetupOutput
from ats.synthesis.synthesizer import Signal

_BIAS_BY_DIRECTION: dict[str, MarketBias] = {"long": "bullish", "short": "bearish"}


def _soft_rules(direction: str, features: Mapping[str, Any]) -> list[Rule]:
    """Thesis-direction soft rules over whichever momentum features are present."""
    rules: list[Rule] = []
    has = lambda k: features.get(k) is not None  # noqa: E731
    if direction == "long":
        if has("rsi_14"):
            rules.append(Rule(left="rsi_14", operator=">", right=48, weight=0.6))
        if has("macd_hist"):
            rules.append(Rule(left="macd_hist", operator=">", right=0, weight=0.4))
    else:
        if has("rsi_14"):
            rules.append(Rule(left="rsi_14", operator="<", right=52, weight=0.6))
        if has("macd_hist"):
            rules.append(Rule(left="macd_hist", operator="<", right=0, weight=0.4))
    return rules


def _invalidation_rules(direction: str, features: Mapping[str, Any]) -> list[InvalidationRule]:
    """A close-confirmed momentum-reversal guard (structural, not a stop restatement)."""
    if features.get("rsi_14") is None:
        return []
    op, level = ("<", 40) if direction == "long" else (">", 60)
    return [
        InvalidationRule(severity="soft", left="rsi_14", operator=op, right=level, on_close=True)
    ]


def signal_to_setup(signal: Signal, features: Mapping[str, Any]) -> SetupOutput:
    """Map a Signal onto one executable SetupOutput."""
    return SetupOutput(
        direction=signal.direction,  # type: ignore[arg-type]
        entry_zone=[signal.entry_zone[0], signal.entry_zone[1]],
        take_profit=list(signal.take_profit),
        stop_loss=signal.stop_loss,
        size_pct=signal.size_pct,
        hard_rules=[],
        soft_rules=_soft_rules(signal.direction, features),
        invalidation_rules=_invalidation_rules(signal.direction, features),
    )


def signal_to_plan(signal: Signal | None, features: Mapping[str, Any]) -> PlanOutput:
    """Map a Signal (or None → stand-aside plan) onto a PlanOutput."""
    if signal is None:
        return PlanOutput(
            market_bias="neutral",
            rationale="Deterministic synthesizer found no qualifying signal.",
            allowed_setups=[],
        )
    bias = _BIAS_BY_DIRECTION[signal.direction]
    rationale = "; ".join(signal.reasons) if signal.reasons else f"deterministic {bias} signal"
    return PlanOutput(
        market_bias=bias,
        rationale=rationale,
        allowed_setups=[signal_to_setup(signal, features)],
    )
