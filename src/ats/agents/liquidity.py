"""Liquidity (M1 proxy) — volume-weighted swing clusters from candles.

The real liquidation-feed agent is M4. Here we stand in with a candle-derived proxy:
find the nearest swing high above and swing low below the current price, weight each by
the volume printed at that pivot (a crude "where stops cluster"), and lean toward the
side that is both closer and heavier. Honest about being a proxy in ``metadata``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ats.agents.base import AgentInput, AgentScore, abstain, clamp01, f, primary_close

_WING = 2


def _swing_clusters(
    bars: list[dict[str, Any]],
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Return (swing_highs, swing_lows) as ``(price, volume)`` at each pivot."""
    highs: list[tuple[float, float]] = []
    lows: list[tuple[float, float]] = []
    if len(bars) < _WING * 2 + 1:
        return highs, lows
    for i in range(_WING, len(bars) - _WING):
        hi = f(bars[i].get("high"))
        lo = f(bars[i].get("low"))
        vol = f(bars[i].get("volume")) or 0.0
        if hi is None or lo is None:
            continue
        left = bars[i - _WING : i]
        right = bars[i + 1 : i + _WING + 1]
        lh = [f(c.get("high")) for c in left + right]
        ll = [f(c.get("low")) for c in left + right]
        if all(v is not None and hi > v for v in lh):
            highs.append((hi, vol))
        if all(v is not None and lo < v for v in ll):
            lows.append((lo, vol))
    return highs, lows


class LiquidityAgent:
    name: ClassVar[str] = "liquidity"
    uses_llm: ClassVar[bool] = False

    def run(self, ai: AgentInput) -> AgentScore:
        close = primary_close(ai)
        bars = ai.recent_ohlcv or []
        atr = f(ai.features.get("atr_14"))
        if close is None or not bars or atr is None or atr <= 0:
            return abstain(self.name, "insufficient_inputs", proxy=True)

        highs, lows = _swing_clusters(bars)
        above = [(p, v) for p, v in highs if p > close]
        below = [(p, v) for p, v in lows if p < close]
        nearest_above = min(above, key=lambda t: t[0] - close) if above else None
        nearest_below = max(below, key=lambda t: t[0]) if below else None

        if nearest_above is None and nearest_below is None:
            return abstain(self.name, "no_clusters", proxy=True)

        # Distance in ATR (closer = heavier pull) × cluster volume.
        def pull(cluster: tuple[float, float] | None) -> float:
            if cluster is None:
                return 0.0
            price, vol = cluster
            dist_atr = abs(price - close) / atr
            return vol / (1.0 + dist_atr)

        pull_up = pull(nearest_below)   # support below → price drawn up
        pull_down = pull(nearest_above)  # resistance above → price drawn down
        total = pull_up + pull_down
        if total <= 0:
            return abstain(self.name, "no_volume", proxy=True)

        imbalance = (pull_up - pull_down) / total  # -1..1
        direction = "neutral"
        if imbalance > 0.2:
            direction = "long"
        elif imbalance < -0.2:
            direction = "short"
        # Proxy stays modest: cap at 0.6 so a noisy stand-in can't dominate the vote.
        score = clamp01(abs(imbalance)) * 0.6 if direction != "neutral" else 0.0
        return AgentScore(
            agent=self.name,
            score=round(score, 6),
            direction=direction,
            deterministic_score=round(score, 6),
            metadata={
                "proxy": True,
                "nearest_support": round(nearest_below[0], 8) if nearest_below else None,
                "nearest_resistance": round(nearest_above[0], 8) if nearest_above else None,
                "imbalance": round(imbalance, 6),
            },
        )
