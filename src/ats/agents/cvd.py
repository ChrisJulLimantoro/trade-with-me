"""CVD — cumulative volume-delta divergence vs price.

Flow-based signal: when price trends up but CVD trends flat/down, aggressive buyers
aren't confirming the move (distribution → short). Symmetric for a down-move not
confirmed by selling (accumulation → long). Score is derived from the slope divergence
between price and CVD over the lookback window — price extremes are no longer required,
making this a leading flow signal rather than a lagging divergence detector.

Falls back to the ``pr_cvd_divergence`` precomputed feature when ``cvd_slope_10``
is unavailable.
"""

from __future__ import annotations

from typing import ClassVar

from ats.agents.base import AgentInput, AgentScore, abstain, clamp01, f, primary_close

_WINDOW = 10        # bars for slope computation (uses cvd_slope_10 + price slope)
_SCALE = 50.0       # price-slope normalizer (bps units keep numbers tractable)


class CvdAgent:
    name: ClassVar[str] = "cvd"
    uses_llm: ClassVar[bool] = False

    def run(self, ai: AgentInput) -> AgentScore:
        feats = ai.features or {}
        cvd_slope = f(feats.get("cvd_slope_10"))
        divergence = f(feats.get("pr_cvd_divergence"))

        if cvd_slope is None and divergence is None:
            return abstain(self.name, "no_cvd")

        close = primary_close(ai)
        bars = ai.recent_ohlcv or []
        if close is None or len(bars) < 2:
            return abstain(self.name, "insufficient_bars")

        # Price slope over _WINDOW bars in bps (normalised so scale is stable).
        lookback = bars[-(_WINDOW + 1) : -1] if len(bars) > _WINDOW else bars[:-1]
        anchor_close = f(lookback[0].get("close")) if lookback else None
        if anchor_close and anchor_close > 0:
            price_slope = (close - anchor_close) / anchor_close * 10_000  # bps
        else:
            price_slope = 0.0

        # Slope divergence: price_slope and cvd_slope have opposite signs → divergence.
        direction = "neutral"
        if cvd_slope is not None:
            # price up, CVD down/flat = distribution → short
            if price_slope > 0 and cvd_slope < 0:
                direction = "short"
            # price down, CVD up/flat = accumulation → long
            elif price_slope < 0 and cvd_slope > 0:
                direction = "long"

            if direction != "neutral":
                # Magnitude: how far apart the two slopes are, normalised.
                div_magnitude = clamp01(abs(price_slope / _SCALE) + clamp01(abs(cvd_slope)))
                score = clamp01(round(div_magnitude / 2.0, 6))
            else:
                score = 0.0
        else:
            # Fallback: use precomputed divergence feature with old extreme-gate stripped.
            if price_slope > 0 and divergence > 0:
                direction = "short"
            elif price_slope < 0 and divergence > 0:
                direction = "long"
            score = clamp01(divergence) if direction != "neutral" else 0.0
            score = round(score, 6)

        return AgentScore(
            agent=self.name,
            score=score,
            direction=direction,
            deterministic_score=score,
            metadata={
                "price_slope_bps": round(price_slope, 4),
                "cvd_slope_10": cvd_slope,
                "pr_cvd_divergence": round(divergence, 6) if divergence is not None else None,
                "divergence_type": "bearish" if direction == "short" else (
                    "bullish" if direction == "long" else "none"
                ),
            },
        )
