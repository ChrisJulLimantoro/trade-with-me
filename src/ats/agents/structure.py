"""Structure — 20-bar breakout / breakdown on 15m with 1h confluence.

Proximity gradient: approaching the 20-bar high/low within 0.5 ATR scores partially
even before the close breaks the level (catches retest entries). Volume is still
required for any direction vote, but the floor is lowered from z=1.5 to z=1.0 so a
valid breakout with mild volume still registers. For confirmed breakouts the original
score formula is preserved; near-breakouts (approaching) score lower.
"""

from __future__ import annotations

from typing import ClassVar

from ats.agents.base import AgentInput, AgentScore, abstain, clamp01, f, primary_close

_BREAKOUT_BARS = 20
_VOL_Z_FLOOR = 1.0      # lowered from 1.5 — a valid breakout with mild vol still scores
_PROXIMITY_ATR = 0.5    # within this many ATRs of the level = near-breakout


class StructureAgent:
    name: ClassVar[str] = "structure"
    uses_llm: ClassVar[bool] = False

    def run(self, ai: AgentInput) -> AgentScore:
        close = primary_close(ai)
        bars = ai.recent_ohlcv or []
        if close is None or len(bars) < _BREAKOUT_BARS + 1:
            return abstain(self.name, "insufficient_bars")

        window = bars[-(_BREAKOUT_BARS + 1) : -1]
        highs = [f(c.get("high")) for c in window]
        lows = [f(c.get("low")) for c in window]
        highs = [x for x in highs if x is not None]
        lows = [x for x in lows if x is not None]
        if not highs or not lows:
            return abstain(self.name, "missing_ohlc")
        prior_high, prior_low = max(highs), min(lows)

        atr = f(ai.features.get("atr_14")) or 0.0
        vol_z = f(ai.features.get("vol_zscore_20"))
        vol_ok = vol_z is not None and vol_z >= _VOL_Z_FLOOR

        if not vol_ok:
            return AgentScore(
                agent=self.name,
                score=0.0,
                direction="neutral",
                deterministic_score=0.0,
                metadata={
                    "prior_high": round(prior_high, 8),
                    "prior_low": round(prior_low, 8),
                    "vol_zscore": vol_z,
                    "reason": "no_breakout",
                },
            )

        direction = "neutral"
        extension = 0.0   # ATRs beyond the level (>0 = confirmed break)
        approaching = False

        if close > prior_high:
            direction = "long"
            extension = (close - prior_high) / atr if atr > 0 else 0.0
        elif close < prior_low:
            direction = "short"
            extension = (prior_low - close) / atr if atr > 0 else 0.0
        elif atr > 0:
            long_dist = (prior_high - close) / atr
            short_dist = (close - prior_low) / atr
            if long_dist <= _PROXIMITY_ATR and long_dist <= short_dist:
                direction = "long"
                approaching = True
            elif short_dist <= _PROXIMITY_ATR:
                direction = "short"
                approaching = True

        if direction == "neutral":
            return AgentScore(
                agent=self.name,
                score=0.0,
                direction="neutral",
                deterministic_score=0.0,
                metadata={
                    "prior_high": round(prior_high, 8),
                    "prior_low": round(prior_low, 8),
                    "vol_zscore": vol_z,
                    "reason": "no_breakout",
                },
            )

        vol_factor = clamp01((vol_z - _VOL_Z_FLOOR) / 2.0)

        if not approaching:
            # Confirmed breakout: original formula, lower vol threshold.
            base = 0.5 + 0.3 * clamp01(extension) + 0.15 * vol_factor
        else:
            # Near-breakout (approaching): lower conviction.
            base = 0.3 + 0.1 * vol_factor

        score = clamp01(round(base + self._htf_confluence(ai, direction), 6))
        return AgentScore(
            agent=self.name,
            score=score,
            direction=direction,
            deterministic_score=score,
            metadata={
                "prior_high": round(prior_high, 8),
                "prior_low": round(prior_low, 8),
                "breakout_extension_atr": round(extension, 6),
                "vol_zscore": vol_z,
                "approaching": approaching,
            },
        )

    def _htf_confluence(self, ai: AgentInput, direction: str) -> float:
        """Small bonus when the 1h trend (close vs its EMA-50) aligns with the breakout."""
        h1 = (ai.higher_timeframes or {}).get("1h") or {}
        feats = h1.get("features") or {}
        ema50 = f(feats.get("ema_50"))
        close1h = f(feats.get("close")) or f(feats.get("price"))
        if ema50 is None or close1h is None:
            return 0.0
        if direction == "long" and close1h > ema50:
            return 0.05
        if direction == "short" and close1h < ema50:
            return 0.05
        return 0.0
