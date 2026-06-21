"""Deterministic proposer — run the 8 agents + synthesizer over a plan envelope.

This is the production proposer (and the control group the system never had). It reads
the SAME envelope ``build_envelope`` already assembles, so the deterministic strategist is
a drop-in: ``planning.create_plan`` runs ``propose_signal`` here, layers the Part-2 bounded
adjudication on top, and keeps the persist / audit path unchanged. ``propose_plan`` returns
the unadjudicated ``(PlanOutput, LlmResult)`` baseline (the control group).
"""

from __future__ import annotations

from typing import Any

from ats.agents import AGENTS
from ats.agents.base import AgentInput
from ats.config import settings
from ats.llm.schemas import LlmResult, PlanOutput
from ats.logging import get_logger
from ats.orchestration.weights import WEIGHTS
from ats.strategy.bridge import signal_to_plan
from ats.synthesis.synthesizer import Signal, synthesize

log = get_logger(__name__)


def _agent_input(envelope: dict[str, Any], symbol: str) -> AgentInput:
    return AgentInput(
        symbol=symbol,
        cycle_ts=envelope.get("as_of"),
        timeframe_primary=envelope.get("timeframe", "15m"),
        features=envelope.get("features") or {},
        recent_ohlcv=envelope.get("recent_ohlcv") or [],
        regime=envelope.get("regime") or {},
        higher_timeframes=envelope.get("higher_timeframes") or {},
    )


def propose_signal(envelope: dict[str, Any], *, symbol: str) -> Signal | None:
    """Run the 8 agents + synthesizer over the envelope and apply the hard regime gate.

    Returns the synthesized ``Signal`` (which still carries the raw deterministic confidence
    ``C`` the Part-2 judge will adjudicate), or ``None`` when no signal qualifies. Pure: the
    same envelope yields a byte-identical Signal.
    """
    ai = _agent_input(envelope, symbol)
    features = ai.features

    scores = {}
    for agent in AGENTS:
        s = agent.run(ai)
        scores[agent.name] = s
        log.info("agent_score", agent=agent.name, score=round(s.score, 6), direction=s.direction)

    risk_limits = envelope.get("risk_limits") or {}
    preferred = risk_limits.get("preferred_direction")
    allowed = risk_limits.get("allowed_directions")

    signal = synthesize(
        scores,
        ai.regime,
        features,
        ai.recent_ohlcv,
        weights=WEIGHTS,
        min_rr=settings.risk.min_rr,
        min_confidence=settings.plan.signal_min_confidence,
        min_stop_atr_mult=settings.risk.min_stop_atr_mult,
        fee_bps=settings.risk.fee_bps,
        slippage_bps=settings.risk.slippage_bps,
        reward_atr_mult=settings.risk.reward_atr_mult,
        preferred_direction=preferred if settings.plan.deterministic_direction_hint else None,
        log=log,
    )

    # Respect the hard regime gate: a direction the runtime filter would discard is dropped
    # here so we don't emit a setup that can never trade.
    if signal is not None and allowed is not None and signal.direction not in allowed:
        log.info(
            "signal_rejected",
            reason="regime_gate",
            direction=signal.direction,
            allowed_directions=allowed,
        )
        return None
    return signal


def propose_plan(envelope: dict[str, Any], *, symbol: str) -> tuple[PlanOutput, LlmResult]:
    """Run the deterministic signal engine and bridge it to a PlanOutput (control group).

    This is the unadjudicated baseline — the Part-2 bounded judge is layered on top in
    ``planning.create_plan``, never here, so this stays the pure deterministic control.
    """
    signal = propose_signal(envelope, symbol=symbol)
    plan = signal_to_plan(signal, envelope.get("features") or {})
    result = LlmResult(
        parse_ok=True,
        model="deterministic",
        mock=True,
        raw=plan.model_dump(mode="json"),
    )
    return plan, result
