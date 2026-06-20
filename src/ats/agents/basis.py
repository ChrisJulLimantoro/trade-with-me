"""Basis — perp premium over spot index (mean-reversion at extremes).

Binance's ``premiumIndex`` measures how far the perp has decoupled from spot. When the
30d z-score of premium is extreme, late longs/shorts have stacked the perp at a
premium/discount that historically mean-reverts: fade rich (short) / cheap (long).
Abstains when ``basis_z_30d`` is NULL (basis row missing or warm-up incomplete).
"""

from __future__ import annotations

from typing import ClassVar

from ats.agents.base import AgentInput, AgentScore, abstain, clamp01, f

_Z_EXTREME = 2.0


class BasisAgent:
    name: ClassVar[str] = "basis"
    uses_llm: ClassVar[bool] = False

    def run(self, ai: AgentInput) -> AgentScore:
        feats = ai.features or {}
        z = f(feats.get("basis_z_30d"))
        if z is None:
            return abstain(self.name, "no_basis")

        direction = "short" if z > _Z_EXTREME else "long" if z < -_Z_EXTREME else "neutral"
        score = clamp01(abs(z) / 3.0) if direction != "neutral" else 0.0
        return AgentScore(
            agent=self.name,
            score=round(score, 6),
            direction=direction,
            deterministic_score=round(score, 6),
            metadata={
                "premium_index": f(feats.get("basis_premium")),
                "basis_z": z,
                **({} if direction != "neutral" else {"reason": "basis_not_extreme"}),
            },
        )
