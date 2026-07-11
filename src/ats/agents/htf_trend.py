"""HTF trend — the dominant higher-timeframe trend as a first-class voter.

The other eight agents read the 15m row, so on an intraday bounce inside a slow
downtrend they vote long and the synthesizer fills a fade that the larger trend
reverses — the dominant BTC-replay loss bucket. This agent injects a mid-chart
direction (1h first, then 4h: close vs EMA50) into the weighted vote so the
synthesizer is biased with the trend without waiting for the ~8-day 4h EMA50 to
flip. Conviction scales with how far price sits from the EMA in ATR units.

The plan-time ``htf_trend_filter`` gate remains on the slower 4h EMA50/EMA200
stack — this agent only changes the vote, not ``allowed_directions``.
"""

from __future__ import annotations

from typing import ClassVar

from ats.agents.base import AgentInput, AgentScore, abstain, clamp01, f

# Distance (in ATR) from the slow EMA at which trend conviction saturates to 1.0.
_FULL_CONVICTION_ATR = 2.0


class HtfTrendAgent:
    name: ClassVar[str] = "htf_trend"
    uses_llm: ClassVar[bool] = False

    def run(self, ai: AgentInput) -> AgentScore:
        htf = ai.higher_timeframes or {}
        for tf in ("1h", "4h"):
            feats = (htf.get(tf) or {}).get("features") or {}
            close = f(feats.get("close")) or f(feats.get("price"))
            ema50 = f(feats.get("ema_50"))
            if close is None or ema50 is None:
                continue
            atr = f(feats.get("atr_14"))
            if atr and atr > 0:
                conviction = clamp01(abs(close - ema50) / (atr * _FULL_CONVICTION_ATR))
            else:
                conviction = 0.5
            score = clamp01(round(0.5 + 0.5 * conviction, 6))
            direction = "long" if close > ema50 else "short"
            return AgentScore(
                agent=self.name,
                score=score,
                direction=direction,
                deterministic_score=score,
                metadata={"tf": tf, "close": close, "ema_50": ema50, "atr_14": atr},
            )
        return abstain(self.name, "no_htf_ema50")
