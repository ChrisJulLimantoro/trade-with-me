"""Structure — 20-bar breakout / breakdown on 15m with 1h confluence.

A close beyond the prior 20-bar high/low backed by a volume expansion
(``vol_zscore_20 > 1.5``) is the breakout. The 1h trend (its EMA-50 alignment) is a
confluence bonus: a 15m breakout that runs with the 1h trend scores higher than 15m
noise against it. Pivots/levels are read on 15m; 1h enters only as bias.
"""

from __future__ import annotations

from typing import ClassVar

from ats.agents.base import AgentInput, AgentScore, abstain, clamp01, f, primary_close

_BREAKOUT_BARS = 20
_VOL_Z_FLOOR = 1.5


class StructureAgent:
    name: ClassVar[str] = "structure"
    uses_llm: ClassVar[bool] = False

    def run(self, ai: AgentInput) -> AgentScore:
        close = primary_close(ai)
        bars = ai.recent_ohlcv or []
        if close is None or len(bars) < _BREAKOUT_BARS + 1:
            return abstain(self.name, "insufficient_bars")

        window = bars[-(_BREAKOUT_BARS + 1) : -1]  # prior N closed bars, excluding current
        highs = [f(c.get("high")) for c in window]
        lows = [f(c.get("low")) for c in window]
        highs = [x for x in highs if x is not None]
        lows = [x for x in lows if x is not None]
        if not highs or not lows:
            return abstain(self.name, "missing_ohlc")
        prior_high, prior_low = max(highs), min(lows)

        atr = f(ai.features.get("atr_14")) or 0.0
        vol_z = f(ai.features.get("vol_zscore_20"))
        vol_ok = vol_z is not None and vol_z > _VOL_Z_FLOOR

        direction = "neutral"
        extension = 0.0
        if close > prior_high and vol_ok:
            direction = "long"
            extension = (close - prior_high) / atr if atr > 0 else 0.0
        elif close < prior_low and vol_ok:
            direction = "short"
            extension = (prior_low - close) / atr if atr > 0 else 0.0

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

        vol_factor = clamp01((vol_z - _VOL_Z_FLOOR) / 1.5)  # 1.5→0, 3.0→1
        base = 0.5 + 0.3 * clamp01(extension) + 0.15 * vol_factor
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
