"""Direction vote + weighted statistics over the eight agent scores.

Abstaining agents (score 0, neutral) are still passed in: they contribute 0 to every
weighted sum and cast no direction vote. The vote is a weighted majority of the
non-neutral agents; ties / all-neutral return ``neutral`` (the synthesizer then rejects).
"""

from __future__ import annotations

from collections.abc import Mapping

from ats.agents.base import AgentScore


def _weighted_pressure(
    scores: Mapping[str, AgentScore], weights: Mapping[str, float]
) -> tuple[float, float]:
    """Return (long_pressure, short_pressure) = Σ weight·score on each side."""
    longp = shortp = 0.0
    for name, s in scores.items():
        w = weights.get(name, 0.0)
        if s.direction == "long":
            longp += w * s.score
        elif s.direction == "short":
            shortp += w * s.score
    return longp, shortp


def direction_vote(
    scores: Mapping[str, AgentScore], weights: Mapping[str, float]
) -> str:
    """Weighted majority direction, or ``neutral`` on a tie / all-neutral."""
    longp, shortp = _weighted_pressure(scores, weights)
    if longp == shortp:
        return "neutral"
    return "long" if longp > shortp else "short"


def weighted_mean(
    scores: Mapping[str, AgentScore], weights: Mapping[str, float], direction: str
) -> float:
    """Weighted mean of scores from agents voting the chosen ``direction``.

    Normalized by the weight mass of the agreeing agents (not the full 1.0), so base
    confidence reflects the conviction of the agents that actually agree, regardless of
    how many abstained.
    """
    num = den = 0.0
    for name, s in scores.items():
        if s.direction != direction:
            continue
        w = weights.get(name, 0.0)
        num += w * s.score
        den += w
    return num / den if den > 0 else 0.0


def score_variance(
    scores: Mapping[str, AgentScore], direction: str
) -> float:
    """Variance of the agreeing agents' scores (drives the alignment penalty).

    Disagreement *within* the winning side (some strongly convinced, some barely) widens
    the variance and should cost confidence, so the synthesizer penalizes it.
    """
    vals = [s.score for s in scores.values() if s.direction == direction]
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / len(vals)
    return sum((v - mean) ** 2 for v in vals) / len(vals)
