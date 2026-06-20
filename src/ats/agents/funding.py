"""Funding — fade extreme Binance funding.

A high positive ``funding_z_30d`` means longs are crowded and paying up → fade with a
short; a deeply negative z means shorts are crowded → fade with a long. OI buildup in
the crowd's direction makes the crowd more crowded, nudging the score up.
"""

from __future__ import annotations

from typing import ClassVar

from ats.agents.base import AgentInput, AgentScore, abstain, clamp01, f

_Z_EXTREME = 2.0


class FundingAgent:
    name: ClassVar[str] = "funding"
    uses_llm: ClassVar[bool] = False

    def run(self, ai: AgentInput) -> AgentScore:
        feats = ai.features or {}
        z = f(feats.get("funding_z_30d"))
        if z is None:
            return abstain(self.name, "no_funding_z")

        direction = "short" if z > _Z_EXTREME else "long" if z < -_Z_EXTREME else "neutral"
        if direction == "neutral":
            return AgentScore(
                agent=self.name,
                score=0.0,
                direction="neutral",
                deterministic_score=0.0,
                metadata={"funding_z_30d": z, "reason": "funding_not_extreme"},
            )

        score = clamp01(abs(z) / 3.0)
        # Crowd confirmation: OI building in the crowd's direction (longs crowded → OI up)
        # makes the fade marginally more reliable.
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
