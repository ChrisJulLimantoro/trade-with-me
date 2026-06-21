"""Momentum — composite momentum + RSI/MACD state on the 15m row.

Direction: long if ``rsi_14 > 50`` and ``macd_hist > 0``; short if ``rsi_14 < 50``
and ``macd_hist < 0``. Each component is scored independently and combined (0.6 RSI,
0.4 MACD), so a bar where RSI is 52 and MACD just turned positive still contributes
rather than scoring 0.
"""

from __future__ import annotations

from typing import ClassVar

from ats.agents.base import AgentInput, AgentScore, abstain, clamp01, f

_RSI_MID = 50.0
_RSI_SCALE = 15.0   # RSI distance to normalize: 50→0, 65→1.0 (long side)
_RSI_LONG = 50.0
_RSI_SHORT = 50.0


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

        # Direction gate: RSI above/below 50 and MACD histogram sign must agree.
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

        # RSI component: graduated from the 50 midline, full conviction at ±20 from 50.
        rsi_score = clamp01(abs(rsi - _RSI_MID) / _RSI_SCALE)

        # MACD component: normalize by momentum_composite if available, else RSI fallback.
        if mc is not None:
            macd_score = clamp01(abs(mc - 0.5) * 2.0)
        else:
            macd_score = clamp01(abs(rsi - _RSI_MID) / _RSI_SCALE)

        score = clamp01(round(0.6 * rsi_score + 0.4 * macd_score, 6))
        return AgentScore(
            agent=self.name,
            score=score,
            direction=direction,
            deterministic_score=score,
            metadata={
                "rsi_14": rsi,
                "macd_hist": round(macd_hist, 8),
                "momentum_composite": mc,
                "rsi_score": round(rsi_score, 6),
                "macd_score": round(macd_score, 6),
            },
        )
