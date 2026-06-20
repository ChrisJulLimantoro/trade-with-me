"""PriceAction (FVG) — 3-candle Fair Value Gap detection + entry-zone override.

A 3-candle FVG is unfilled imbalance: an impulse so strong that ``candle[i-2].high <
candle[i].low`` (bullish) or ``candle[i-2].low > candle[i].high`` (bearish). The most
recent fresh gap in the last 5 closed 15m candles emits a ``fvg_zone`` the synthesizer
may adopt as a tighter entry band (the only place a single agent changes signal shape).

The direction-alignment bonus from spec 04 is applied by the synthesizer (it only
overrides when ``pa.direction == synth_direction``), so this agent scores the raw gap
size and stays direction-pure. A candle that *closed through* the gap invalidates it.
"""

from __future__ import annotations

from typing import ClassVar

from ats.agents.base import AgentInput, AgentScore, abstain, clamp01, f

_WINDOW = 5


class PriceActionAgent:
    name: ClassVar[str] = "price_action"
    uses_llm: ClassVar[bool] = False

    def run(self, ai: AgentInput) -> AgentScore:
        bars = ai.recent_ohlcv or []
        atr = f(ai.features.get("atr_14"))
        if len(bars) < 3 or atr is None or atr <= 0:
            return abstain(self.name, "insufficient_bars")

        window = bars[-_WINDOW:] if len(bars) >= _WINDOW else bars
        gap = self._latest_fvg(window)
        if gap is None:
            return abstain(self.name, "no_fvg")

        direction, low, high, i_gap = gap
        # Close-mitigation invalidation: the most recent candle closed back through the gap.
        last_close = f(bars[-1].get("close"))
        if last_close is not None and low <= last_close <= high:
            return abstain(self.name, "fvg_mitigated")

        gap_size = high - low
        relative_size = gap_size / atr
        score = clamp01(round(relative_size, 6))
        fvg_zone = [round(low, 8), round(high, 8)]
        return AgentScore(
            agent=self.name,
            score=score,
            direction=direction,
            deterministic_score=score,
            metadata={
                "fvg_zone": fvg_zone,
                "fvg_age_bars": len(window) - 1 - i_gap,
                "relative_size_atr": round(relative_size, 6),
            },
        )

    def _latest_fvg(
        self, window: list[dict]
    ) -> tuple[str, float, float, int] | None:
        """Most recent (direction, zone_low, zone_high, index) gap in the window."""
        result: tuple[str, float, float, int] | None = None
        for i in range(2, len(window)):
            h2 = f(window[i - 2].get("high"))
            l2 = f(window[i - 2].get("low"))
            hi = f(window[i].get("high"))
            lo = f(window[i].get("low"))
            if None in (h2, l2, hi, lo):
                continue
            if h2 < lo:  # bullish gap below current price: [prev_high, cur_low]
                result = ("long", h2, lo, i)
            elif l2 > hi:  # bearish gap above current price: [cur_high, prev_low]
                result = ("short", hi, l2, i)
        return result
