"""Plan-time bounded veto/bias (spec 07, Part 2 Role 1).

The deterministic synthesizer (Part 1) produces a ``Signal`` with confidence ``C``. The
LLM judge returns an ``AdjudicationOutput``; this module applies it **deterministically,
after synthesis**:

    final_conf = clamp(C + clamp(delta, -0.20, +0.20), 0, 1)
    if no_trade or final_conf < conf_threshold:  veto (no entry this window)
    size_pct = size_for(final_conf)

Direction, entry_zone, stop_loss and take_profit are **never** read from the LLM. So the
judge can pull a marginal signal under the threshold (or ``no_trade`` it outright) and nudge
a strong one's size, but it cannot create a trade the agents didn't propose, flip its
direction, or move its levels — a ±0.20 move cannot manufacture edge, which is what makes the
−36%→+83% replay swing mathematically impossible.

This module imports only the synthesizer + the LLM *schema* (the shared contract), never the
LLM transport, so the strategy layer stays free of network dependencies.
"""

from __future__ import annotations

from dataclasses import replace

from ats.llm.schemas import AdjudicationOutput
from ats.synthesis.synthesizer import Signal, size_for

# The hard ±0.20 budget (spec 07 §"Hard ±0.20 clamp"). Applied in code regardless of what
# the model returns, so the bound is guaranteed and auditable.
CONFIDENCE_DELTA_CLAMP = 0.20

_BIAS_BY_DIRECTION: dict[str, str] = {"long": "bullish", "short": "bearish"}


def adjudication_envelope(envelope: dict, signal: Signal) -> dict:
    """The compact judge envelope: the deterministic signal + market context.

    Deliberately carries no authorable levels for the model to "improve" — only the signal's
    summary (direction, confidence, RR, per-agent scores, reasons) and read-only context.
    """
    return {
        "as_of": envelope.get("as_of"),
        "symbol": envelope.get("symbol"),
        "timeframe": envelope.get("timeframe"),
        "signal": {
            "direction": signal.direction,
            "confidence": signal.confidence,
            "risk_reward": signal.risk_reward,
            "alignment_pen": signal.alignment_pen,
            "agent_scores": signal.agent_scores,
            "reasons": signal.reasons,
            "fvg_override": signal.fvg_override,
        },
        "regime": envelope.get("regime"),
        "features": envelope.get("features"),
        "higher_timeframes": envelope.get("higher_timeframes"),
        "planner_context": envelope.get("planner_context"),
        "prior_lessons": envelope.get("prior_lessons"),
    }


def apply_adjudication(
    signal: Signal,
    adj: AdjudicationOutput,
    *,
    min_confidence: float,
    log=None,
) -> Signal | None:
    """Apply the bounded judgement to ``signal``; return the adjusted Signal or ``None`` (veto).

    The delta is clamped to ±``CONFIDENCE_DELTA_CLAMP`` here — never trusting the raw model
    value — and ``no_trade`` or a sub-threshold final confidence vetoes the trade. The
    direction and all price levels are copied through untouched.
    """
    c = signal.confidence
    raw_delta = adj.confidence_delta
    delta = max(-CONFIDENCE_DELTA_CLAMP, min(CONFIDENCE_DELTA_CLAMP, raw_delta))
    final = max(0.0, min(1.0, c + delta))
    bias_agrees = adj.bias == _BIAS_BY_DIRECTION.get(signal.direction)
    vetoed = adj.no_trade or final < min_confidence

    if log is not None:
        log.info(
            "adjudication",
            direction=signal.direction,
            det_conf=round(c, 6),
            delta_raw=round(raw_delta, 6),
            delta=round(delta, 6),
            final_conf=round(final, 6),
            no_trade=adj.no_trade,
            bias=adj.bias,
            bias_agrees=bias_agrees,
            vetoed=vetoed,
        )
    if vetoed:
        return None

    reasons = list(adj.reasons) if adj.reasons else list(signal.reasons)
    return replace(
        signal,
        confidence=round(final, 6),
        size_pct=size_for(final),
        reasons=reasons,
        metadata={
            **signal.metadata,
            "det_confidence": round(c, 6),
            "adjudication_delta": round(delta, 6),
            "adjudication_bias": adj.bias,
            "adjudication_bias_agrees": bias_agrees,
        },
    )
