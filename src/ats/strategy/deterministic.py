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
from ats.agents.base import AgentInput, AgentScore
from ats.config import settings
from ats.llm.schemas import LlmResult, PlanOutput
from ats.logging import get_logger
from ats.orchestration.weights import WEIGHTS, weights_with_htf
from ats.agents.base import f
from ats.strategy.bridge import signal_to_plan
from ats.synthesis.mean_reversion import propose_mean_reversion
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


def propose_signal(
    envelope: dict[str, Any], *, symbol: str
) -> tuple[Signal | None, dict[str, AgentScore]]:
    """Run the 8 agents + synthesizer over the envelope and apply the hard regime gate.

    Returns ``(signal, agent_scores)``. ``signal`` is the synthesized ``Signal`` (which still
    carries the raw deterministic confidence ``C`` the Part-2 judge will adjudicate), or
    ``None`` when no signal qualifies. ``agent_scores`` is always populated so stand-aside
    plans can surface per-agent detail. Pure: the same envelope yields a byte-identical Signal.
    """
    ai = _agent_input(envelope, symbol)
    features = ai.features

    scores = {}
    for agent in AGENTS:
        s = agent.run(ai)
        scores[agent.name] = s
        log.info(
            "agent_score",
            agent=agent.name,
            score=round(s.score, 6),
            direction=s.direction,
            reason=s.metadata.get("reason"),
        )

    risk_limits = envelope.get("risk_limits") or {}
    preferred = risk_limits.get("preferred_direction")
    allowed = risk_limits.get("allowed_directions")

    # Mean-reversion router: in low-vol sideways chop (regime "side-*" AND absolute atr_pct at/below
    # the gate) the trend-pullback engine has no edge, so try the counter-trend range-fade proposer
    # first. The absolute-atr_pct gate confines it to a structurally low-vol symbol (BTC). On a
    # qualifying signal (gated by the hard-regime-only mr_allowed_directions) return it; otherwise
    # fall through to the trend synthesizer, which the chop floor already rejects on most such bars.
    if settings.plan.mr_enabled:
        regime_cell = (ai.regime or {}).get("regime_cell") or ""
        atr_pct = f(features.get("atr_pct"))
        if (
            regime_cell.lower().startswith("side")
            and atr_pct is not None
            and atr_pct <= settings.plan.mr_atr_pct_max
        ):
            mr = propose_mean_reversion(
                features,
                ai.recent_ohlcv,
                range_lookback=settings.plan.mr_range_lookback,
                edge_frac=settings.plan.mr_edge_frac,
                rsi_os=settings.plan.mr_rsi_os,
                rsi_ob=settings.plan.mr_rsi_ob,
                stop_buffer_atr=settings.plan.mr_stop_buffer_atr,
                min_range_atr=settings.plan.mr_min_range_atr,
                min_rr=settings.risk.min_rr,
                fee_bps=settings.risk.fee_bps,
                slippage_bps=settings.risk.slippage_bps,
                pullback_atr_frac=settings.risk.entry_pullback_atr_frac,
                log=log,
            )
            if mr is not None:
                mr_allowed = risk_limits.get("mr_allowed_directions")
                if mr_allowed is None or mr.direction in mr_allowed:
                    return mr, scores
                log.info(
                    "signal_rejected",
                    reason="mr_regime_gate",
                    direction=mr.direction,
                    allowed_directions=mr_allowed,
                )

    signal = synthesize(
        scores,
        ai.regime,
        features,
        ai.recent_ohlcv,
        weights=(
            weights_with_htf(settings.plan.htf_trend_weight)
            if settings.plan.htf_trend_weight is not None
            else WEIGHTS
        ),
        min_rr=settings.risk.min_rr,
        min_confidence=settings.plan.signal_min_confidence,
        chop_atr_pct_max=settings.plan.chop_atr_pct_max,
        chop_min_confidence=settings.plan.chop_min_confidence,
        min_stop_atr_mult=settings.risk.min_stop_atr_mult,
        fee_bps=settings.risk.fee_bps,
        slippage_bps=settings.risk.slippage_bps,
        reward_atr_mult=settings.risk.reward_atr_mult,
        pullback_atr_frac=settings.risk.entry_pullback_atr_frac,
        adaptive_stop_enabled=settings.risk.adaptive_stop_enabled,
        stop_atr_wide=settings.risk.stop_atr_wide,
        stop_vol_lo=settings.risk.stop_vol_lo,
        stop_vol_hi=settings.risk.stop_vol_hi,
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
        return None, scores
    return signal, scores


def propose_plan(envelope: dict[str, Any], *, symbol: str) -> tuple[PlanOutput, LlmResult]:
    """Run the deterministic signal engine and bridge it to a PlanOutput (control group).

    This is the unadjudicated baseline — the Part-2 bounded judge is layered on top in
    ``planning.create_plan``, never here, so this stays the pure deterministic control.
    """
    signal, scores = propose_signal(envelope, symbol=symbol)
    plan = signal_to_plan(signal, envelope.get("features") or {}, agent_scores=scores)
    result = LlmResult(
        parse_ok=True,
        model="deterministic",
        mock=True,
        raw=plan.model_dump(mode="json"),
    )
    return plan, result
