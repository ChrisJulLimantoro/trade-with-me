"""Funding — fade extreme Binance funding.

A high positive ``funding_z_30d`` means longs are crowded and paying up → fade with a
short; a deeply negative z means shorts are crowded → fade with a long. Scoring starts
at z=1.0 (elevated, not just extreme) and ramps to full conviction at z=3.0, so the
agent expresses a mild lean at elevated funding rather than being silent until z=2.
"""

from __future__ import annotations

from typing import ClassVar

from ats.agents.base import AgentInput, AgentScore, abstain, clamp01, f

_Z_START = 1.0    # begin scoring here (was a hard gate at 2.0)
_Z_FULL = 3.0     # full conviction at this z-score


class FundingAgent:
    name: ClassVar[str] = "funding"
    uses_llm: ClassVar[bool] = False

    def run(self, ai: AgentInput) -> AgentScore:
        feats = ai.features or {}
        z = f(feats.get("funding_z_30d"))
        if z is None:
            return abstain(self.name, "no_funding_z")

        if abs(z) <= _Z_START:
            return AgentScore(
                agent=self.name,
                score=0.0,
                direction="neutral",
                deterministic_score=0.0,
                metadata={"funding_z_30d": z, "reason": "funding_not_elevated"},
            )

        direction = "short" if z > 0 else "long"
        score = clamp01((abs(z) - _Z_START) / (_Z_FULL - _Z_START))

        # Crowd confirmation: OI building in the crowd's direction makes the fade more reliable.
        oi = f(feats.get("oi_delta_pct_24h"))
        crowd_building = oi is not None and (
            (direction == "short" and oi > 0) or (direction == "long" and oi < 0)
        )
        if crowd_building:
            score = clamp01(score + 0.05)

        return AgentScore(
            agent=self.name,
            score=round(score, 6),
            direction=direction,
            deterministic_score=round(score, 6),
            metadata={
                "funding_z_30d": z,
                "funding_rate": f(feats.get("funding_rate")),
                "oi_delta_pct_24h": oi,
            },
        )
