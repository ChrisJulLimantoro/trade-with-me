"""Template-rendered ``reasons[]`` — deterministic, no LLM (spec 04 M1).

Each reason cites concrete numbers from the finalized signal / agent scores so the
audit trail is human-readable. Spec 07 swaps this renderer for an LLM call that runs
*after* the signal is frozen (it can phrase, never change a field).
"""

from __future__ import annotations

from collections.abc import Mapping

from ats.agents.base import AgentScore


def render_reasons(
    *,
    direction: str,
    scores: Mapping[str, AgentScore],
    confidence: float,
    risk_reward: float,
    fvg_override: bool,
    entry_zone: list[float],
) -> list[str]:
    """3–5 concrete, template-rendered reason strings for the signal."""
    reasons: list[str] = []
    # Lead with the agents that voted the winning direction, strongest first.
    agreeing = sorted(
        (s for s in scores.values() if s.direction == direction and s.score > 0),
        key=lambda s: s.score,
        reverse=True,
    )
    for s in agreeing[:3]:
        reasons.append(_agent_phrase(s))

    if fvg_override:
        reasons.append(f"entry refined to FVG zone [{entry_zone[0]:g}, {entry_zone[1]:g}]")

    dissent = [s for s in scores.values() if s.direction not in (direction, "neutral")]
    if dissent:
        names = ", ".join(sorted(s.agent for s in dissent))
        reasons.append(f"{names} dissent absorbed by alignment penalty")

    reasons.append(f"{direction} confidence {confidence:.2f}, rr {risk_reward:.2f}")
    return reasons[:5]


def _agent_phrase(s: AgentScore) -> str:
    """One concrete phrase per agent, citing its driving number when present.

    Metadata may be sparse (e.g. a hand-built Signal in the bridge), so every numeric
    field is null-guarded and falls back to a generic phrase rather than crashing.
    """
    m = s.metadata or {}

    def num(key: str, fmt: str) -> str | None:
        v = m.get(key)
        return format(v, fmt) if isinstance(v, (int, float)) else None

    if s.agent == "structure":
        ext, vz = num("breakout_extension_atr", ".2f"), num("vol_zscore", ".2f")
        if ext and vz:
            return f"breakout {ext} ATR beyond range, vol_zscore {vz}"
        return "structural breakout"
    if s.agent == "momentum":
        rsi, mh = num("rsi_14", ".0f"), num("macd_hist", "+.4g")
        return f"momentum: rsi {rsi}, macd_hist {mh}" if rsi and mh else "momentum aligned"
    if s.agent == "funding":
        z = num("funding_z_30d", "+.2f")
        return f"funding z {z} → crowd fade" if z else "funding extreme → fade"
    if s.agent == "cross_venue":
        z = num("divergence_z", "+.2f")
        if z:
            return f"cross-venue funding z {z} vs {m.get('peer_count')} peers"
        return "cross-venue divergence"
    if s.agent == "basis":
        z = num("basis_z", "+.2f")
        return f"basis z {z} → mean-reversion" if z else "basis extreme → fade"
    if s.agent == "cvd":
        return f"{m.get('divergence_type', 'cvd')} CVD divergence ({s.score:.2f})"
    if s.agent == "liquidity":
        imb = num("imbalance", "+.2f")
        return f"liquidity proxy imbalance {imb}" if imb else "liquidity proxy lean"
    if s.agent == "price_action":
        rs = num("relative_size_atr", ".2f")
        return f"fresh FVG, {rs} ATR" if rs else "fresh FVG"
    return f"{s.agent} score {s.score:.2f}"
