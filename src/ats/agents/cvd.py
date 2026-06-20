"""CVD — cumulative volume-delta divergence vs price.

When price prints a new 30-bar high but aggressive buying (CVD) doesn't confirm, the
move is distribution → bearish; symmetric for a new low not confirmed by selling →
bullish. Spec 02 precomputes ``pr_cvd_divergence`` (0..1) as the divergence magnitude;
the new-high/low test runs on the 15m candles. Abstains when ``cvd_30`` is NULL.
"""

from __future__ import annotations

from typing import ClassVar

from ats.agents.base import AgentInput, AgentScore, abstain, clamp01, f, primary_close

_WINDOW = 30


class CvdAgent:
    name: ClassVar[str] = "cvd"
    uses_llm: ClassVar[bool] = False

    def run(self, ai: AgentInput) -> AgentScore:
        feats = ai.features or {}
        cvd_30 = f(feats.get("cvd_30"))
        divergence = f(feats.get("pr_cvd_divergence"))
        if cvd_30 is None or divergence is None:
            return abstain(self.name, "no_cvd")

        close = primary_close(ai)
        bars = ai.recent_ohlcv or []
        if close is None or len(bars) < 2:
            return abstain(self.name, "insufficient_bars")

        tail = bars[-_WINDOW:] if len(bars) >= _WINDOW else bars
        highs = [f(c.get("high")) for c in tail]
        lows = [f(c.get("low")) for c in tail]
        highs = [x for x in highs if x is not None]
        lows = [x for x in lows if x is not None]
        last_high = f(bars[-1].get("high"))
        last_low = f(bars[-1].get("low"))
        price_nh = bool(highs) and last_high is not None and last_high >= max(highs)
        price_nl = bool(lows) and last_low is not None and last_low <= min(lows)

        # A new price extreme that the divergence flags is the unconfirmed move.
        direction = "neutral"
        div_type = "none"
        if price_nh and divergence > 0:
            direction, div_type = "short", "bearish"
        elif price_nl and divergence > 0:
            direction, div_type = "long", "bullish"

        score = clamp01(divergence) if direction != "neutral" else 0.0
        return AgentScore(
            agent=self.name,
            score=round(score, 6),
            direction=direction,
            deterministic_score=round(score, 6),
            metadata={
                "cvd_slope_10": f(feats.get("cvd_slope_10")),
                "divergence_type": div_type,
                "pr_cvd_divergence": round(divergence, 6),
            },
        )
