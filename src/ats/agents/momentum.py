"""Momentum — composite momentum + RSI/MACD state on the 15m row.

Direction: long if ``rsi_14 > 55`` and ``macd_hist > 0``; short if ``rsi_14 < 45`` and
``macd_hist < 0``; else neutral. Conviction comes from ``momentum_composite`` (0..1,
0.5 = neutral), so a strongly-trending bar scores high and a flat one abstains.
"""

from __future__ import annotations

from typing import ClassVar

from ats.agents.base import AgentInput, AgentScore, abstain, clamp01, f

_RSI_LONG = 55.0
_RSI_SHORT = 45.0


class MomentumAgent:
    name: ClassVar[str] = "momentum"
    uses_llm: ClassVar[bool] = False

    def run(self, ai: AgentInput) -> AgentScore:
        feats = ai.features or {}
        rsi = f(feats.get("rsi_14"))
        macd_hist = f(feats.get("macd_hist"))
        mc = f(feats.get("momentum_composite"))
        if rsi is None or macd_hist is None:
            return abstain(self.name, "missing_momentum_inputs")

        direction = "neutral"
        if rsi > _RSI_LONG and macd_hist > 0:
            direction = "long"
        elif rsi < _RSI_SHORT and macd_hist < 0:
            direction = "short"

        if direction == "neutral":
            return AgentScore(
                agent=self.name,
                score=0.0,
                direction="neutral",
                deterministic_score=0.0,
                metadata={"rsi_14": rsi, "macd_hist": macd_hist, "reason": "no_momentum"},
            )

        # Conviction from the composite (0.5 = neutral); fall back to RSI distance.
        if mc is not None:
            conviction = clamp01(abs(mc - 0.5) * 2.0)
        else:
            conviction = clamp01(abs(rsi - 50.0) / 25.0)
        score = clamp01(round(0.5 + 0.5 * conviction, 6))
        return AgentScore(
            agent=self.name,
            score=score,
            direction=direction,
            deterministic_score=score,
            metadata={
                "rsi_14": rsi,
                "macd_hist": round(macd_hist, 8),
                "momentum_composite": mc,
            },
        )
