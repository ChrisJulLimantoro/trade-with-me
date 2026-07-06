"""Synthesizer — eight AgentScores → one structured Signal (spec 04 §Synthesizer).

Pipeline: direction vote → base confidence → alignment penalty → regime modulation →
SL/TP → FVG entry-zone override → RR floor → confidence threshold → confidence-tiered
sizing. Pure function of its inputs, so identical input yields a byte-identical Signal.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ats.agents.base import AgentScore, clamp01, f
from ats.synthesis.direction import direction_vote, score_variance, weighted_mean
from ats.synthesis.reasons import render_reasons
from ats.synthesis.sl_tp import adaptive_stop_mult, compute_levels, default_band


@dataclass
class Signal:
    direction: str
    entry_zone: list[float]
    stop_loss: float
    take_profit: list[float]
    confidence: float
    size_pct: float
    reasons: list[str]
    agent_scores: dict[str, float]
    risk_reward: float
    alignment_pen: float
    fvg_override: bool = False
    worst_case_fill: float = 0.0
    metadata: dict = field(default_factory=dict)


def size_for(conf: float) -> float:
    """Confidence-tiered advisory size (risk-based sizing overrides downstream)."""
    if conf >= 0.90:
        return 0.05
    if conf >= 0.80:
        return 0.04
    if conf >= 0.70:
        return 0.03
    return 0.02


def _regime_modulation(conf: float, cell: str | None, direction: str) -> float:
    """Spec 04 regime modulation + symmetric short counterparts."""
    if cell == "bear-high" and direction == "long":
        conf *= 0.85
    elif cell == "bull-low" and direction == "long":
        conf *= 1.05
    elif cell == "bull-high" and direction == "short":
        conf *= 0.85
    elif cell == "bear-low" and direction == "short":
        conf *= 1.05
    return conf


def synthesize(
    scores: Mapping[str, AgentScore],
    regime: Mapping,
    features: Mapping,
    recent_ohlcv: list,
    *,
    weights: Mapping[str, float],
    min_rr: float,
    min_confidence: float,
    chop_atr_pct_max: float = 0.0,
    chop_min_confidence: float = 0.0,
    min_stop_atr_mult: float,
    fee_bps: float,
    slippage_bps: float,
    reward_atr_mult: float = 3.0,
    pullback_atr_frac: float = 0.25,
    adaptive_stop_enabled: bool = False,
    stop_atr_wide: float = 0.0,
    stop_vol_lo: float = 0.0,
    stop_vol_hi: float = 0.0,
    preferred_direction: str | None = None,
    min_voting_agents: int = 0,
    log=None,
) -> Signal | None:
    """Combine the agent scores into a Signal, or return None (rejected)."""
    agent_scores = {name: round(s.score, 6) for name, s in scores.items()}

    # 1. Direction (weighted majority); preferred_direction breaks an exact tie only.
    direction = direction_vote(scores, weights)
    if direction == "neutral":
        if preferred_direction in ("long", "short") and _is_tie(scores, weights):
            direction = preferred_direction
        else:
            _reject(log, "neutral_vote", agent_scores)
            return None

    # 1b. Minimum voting agents (i11 mechanism, default 0 = OFF). Reject signals where
    # fewer than ``min_voting_agents`` agents voted in the chosen direction. Kills the
    # lone-agent Q4 whipsaws the i10 conviction-sizing diagnosis surfaced: on the swing
    # profile ~33% of signals are single-agent (one strong voter, seven abstentions) →
    # ``weighted_mean`` of one voter = that voter's score → conf≈1.0 with zero alignment
    # penalty → over-confident whipsaws. Demanding ≥2 voters ensures confirmation before a
    # setup is admissible. A subtractive filter in the same class as ``htf_trend_filter``
    # / ``sideways_block_longs`` / the swing trend-strength gate (all already shipping).
    if min_voting_agents > 0:
        voter_count = sum(1 for s in scores.values() if s.direction == direction)
        if voter_count < min_voting_agents:
            _reject(
                log, "min_voting_agents", agent_scores,
                direction=direction, voters=voter_count, min=min_voting_agents,
            )
            return None

    # 2. Base confidence = weighted mean of the agreeing agents.
    base = weighted_mean(scores, weights, direction)

    # 3. Alignment penalty from intra-side variance.
    variance = score_variance(scores, direction)
    alignment_pen = min(0.5, 1.5 * variance)
    conf = base * (1 - alignment_pen)

    # 4. Regime modulation.
    cell = (regime or {}).get("regime_cell")
    conf = clamp01(_regime_modulation(conf, cell, direction))

    # 5. Default levels.
    close = f(features.get("close")) or f(features.get("price"))
    atr = f(features.get("atr_14"))
    if close is None or atr is None or atr <= 0:
        _reject(log, "no_price_or_atr", agent_scores)
        return None
    entry_zone = default_band(close, atr, direction, pullback_atr_frac=pullback_atr_frac)
    fvg_override = False

    # Volatility-adaptive stop width: widen the stop in locally COMPRESSED volatility (low
    # pr_atr — choppy ranges that whipsaw a tight stop) and keep it tight in locally EXPANDED
    # volatility (high pr_atr — trend legs that pay off). pr_atr is the causal trailing ATR
    # percentile rank. Falls back to the fixed min_stop_atr_mult when disabled or pr_atr is missing.
    stop_mult = adaptive_stop_mult(
        f(features.get("atr_pct")), min_stop_atr_mult, stop_atr_wide,
        stop_vol_lo, stop_vol_hi, adaptive_stop_enabled,
    )

    # 6. FVG entry-zone override — the only single-agent shape change.
    pa = scores.get("price_action")
    if pa is not None and pa.score > 0.6 and pa.direction == direction:
        zone = pa.metadata.get("fvg_zone")
        if zone and len(zone) == 2 and zone[0] < zone[1]:
            entry_zone = [round(float(zone[0]), 8), round(float(zone[1]), 8)]
            fvg_override = True

    levels = compute_levels(
        direction,
        close=close,
        atr=atr,
        entry_zone=entry_zone,
        min_stop_atr_mult=stop_mult,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        reward_atr_mult=reward_atr_mult,
    )

    # 7. RR floor (scalper min_rr, not the spec's hardcoded 2.0).
    if levels.risk_reward < min_rr:
        _reject(log, "rr_floor", agent_scores, rr=levels.risk_reward)
        return None

    # 8. Confidence threshold. In low-volatility chop (absolute ATR/price at/below
    # chop_atr_pct_max) the trend-pullback edge is weakest, so demand a higher floor there;
    # higher-vol bars keep the base floor. Absolute vol distinguishes chop from trend ACROSS
    # symbols without subtracting a regime (which reshuffles non-locally under single-position).
    floor = min_confidence
    if chop_min_confidence > 0.0:
        atr_pct = f(features.get("atr_pct"))
        if atr_pct is not None and atr_pct <= chop_atr_pct_max:
            floor = max(floor, chop_min_confidence)
    if conf < floor:
        _reject(log, "low_conf", agent_scores, conf=round(conf, 6), floor=round(floor, 6))
        return None

    reasons = render_reasons(
        direction=direction,
        scores=scores,
        confidence=conf,
        risk_reward=levels.risk_reward,
        fvg_override=fvg_override,
        entry_zone=levels.entry_zone,
    )
    signal = Signal(
        direction=direction,
        entry_zone=levels.entry_zone,
        stop_loss=levels.stop_loss,
        take_profit=levels.take_profit,
        confidence=round(conf, 6),
        size_pct=size_for(conf),
        reasons=reasons,
        agent_scores=agent_scores,
        risk_reward=levels.risk_reward,
        alignment_pen=round(alignment_pen, 6),
        fvg_override=fvg_override,
        worst_case_fill=levels.worst_case_fill,
        metadata={"regime_cell": cell, "base_confidence": round(base, 6)},
    )
    if log is not None:
        log.info(
            "signal_synthesized",
            direction=direction,
            conf=round(conf, 6),
            rr=levels.risk_reward,
            entry_zone=levels.entry_zone,
            fvg_override=fvg_override,
            alignment_pen=round(alignment_pen, 6),
        )
    return signal


def _is_tie(scores: Mapping[str, AgentScore], weights: Mapping[str, float]) -> bool:
    longp = sum(weights.get(n, 0) * s.score for n, s in scores.items() if s.direction == "long")
    shortp = sum(weights.get(n, 0) * s.score for n, s in scores.items() if s.direction == "short")
    return longp == shortp and longp > 0


def _reject(log, reason: str, agent_scores: dict, **extra) -> None:
    if log is not None:
        log.info("signal_rejected", reason=reason, agent_scores=agent_scores, **extra)
