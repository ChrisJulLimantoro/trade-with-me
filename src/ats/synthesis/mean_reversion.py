"""Mean-reversion proposer — fade the range in low-vol chop.

The trend-pullback engine (``synthesize``) buys dips *expecting continuation*; in a low-volatility
sideways regime there is no continuation, so those entries whipsaw. This proposer is its inverse:
it FADES the established range — limit-buy near the bottom / limit-sell near the top — and targets
reversion to the range midpoint. It runs only behind the router's low-vol-sideways gate
(``deterministic.propose_signal``), and returns the SAME :class:`Signal` the trend path returns, so
the bridge / persistence / exit machine consume it unchanged.

Causality: the range is taken over CLOSED bars strictly BEFORE the decision bar
(``recent_ohlcv[-(N+1):-1]``, the same window :class:`StructureAgent` uses), and the current close
is compared against it — no look-ahead. The entry then fills via the existing resting-limit path.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ats.agents.base import clamp01, f
from ats.synthesis.sl_tp import _round_trip_cost_frac, default_band
from ats.synthesis.synthesizer import Signal, size_for


def propose_mean_reversion(
    features: Mapping[str, Any],
    recent_ohlcv: list[dict[str, Any]],
    *,
    range_lookback: int,
    edge_frac: float,
    rsi_os: float,
    rsi_ob: float,
    stop_buffer_atr: float,
    min_range_atr: float,
    min_rr: float,
    fee_bps: float,
    slippage_bps: float,
    pullback_atr_frac: float = 0.25,
    log=None,
) -> Signal | None:
    """Fade the recent range toward its midpoint, or return None (stand aside).

    Pure: identical inputs yield a byte-identical Signal. Emits structured ``mr_signal`` /
    ``mr_skipped`` logs so the decision is greppable in the replay stream.
    """
    close = f(features.get("close")) or f(features.get("price"))
    atr = f(features.get("atr_14"))
    rsi = f(features.get("rsi_14"))
    if close is None or atr is None or atr <= 0:
        _skip(log, "no_price_or_atr")
        return None
    if rsi is None:
        _skip(log, "no_rsi")
        return None

    # Range over the N CLOSED bars strictly before the decision bar (excludes recent_ohlcv[-1]).
    if len(recent_ohlcv) < range_lookback + 1:
        _skip(log, "insufficient_bars")
        return None
    window = recent_ohlcv[-(range_lookback + 1) : -1]
    highs = [h for c in window if (h := f(c.get("high"))) is not None]
    lows = [low for c in window if (low := f(c.get("low"))) is not None]
    if not highs or not lows:
        _skip(log, "missing_ohlc")
        return None
    band_high, band_low = max(highs), min(lows)
    width = band_high - band_low
    if width <= 0 or width / atr < min_range_atr:
        _skip(log, "range_too_narrow", range_atr=round(width / atr, 4) if atr else None)
        return None
    mid = (band_high + band_low) / 2.0

    # Contrarian trigger: at the bottom edge + oversold → fade up (long); at the top edge +
    # overbought → fade down (short). edge_frac < 0.5, so at most one side can fire.
    long_edge = band_low + edge_frac * width
    short_edge = band_high - edge_frac * width
    if close <= long_edge and rsi <= rsi_os:
        direction = "long"
    elif close >= short_edge and rsi >= rsi_ob:
        direction = "short"
    else:
        _skip(log, "not_in_band", rsi=round(rsi, 2))
        return None

    # Reversion geometry: entry on the favorable (pullback) side of close; stop just beyond the
    # faded band edge; both TP legs at the mid so the trade harvests at the mean (no trail).
    entry_zone = default_band(close, atr, direction, pullback_atr_frac=pullback_atr_frac)
    low, high = entry_zone
    if direction == "long":
        worst = high                         # least-favorable fill for a long
        stop = band_low - stop_buffer_atr * atr
        risk = worst - stop
        reward = mid - worst
    else:
        worst = low                          # least-favorable fill for a short
        stop = band_high + stop_buffer_atr * atr
        risk = stop - worst
        reward = worst - mid

    # Mid must clear the worst-case fill by more than a round-trip's cost, else the reversion
    # target is inside the noise and the trade can't be net positive.
    cost_floor = _round_trip_cost_frac(fee_bps, slippage_bps) * worst
    if risk <= 0 or reward <= cost_floor:
        _skip(log, "target_inside_cost", reward=round(reward, 8))
        return None
    rr = reward / risk
    if rr < min_rr:
        _skip(log, "rr_floor", rr=round(rr, 4))
        return None

    # Confidence: deeper into the edge band + more extreme RSI = higher conviction (drives sizing).
    edge_depth = (long_edge - close) / width if direction == "long" else (close - short_edge) / width
    rsi_extra = (rsi_os - rsi) / max(rsi_os, 1.0) if direction == "long" else (rsi - rsi_ob) / max(100.0 - rsi_ob, 1.0)
    conf = clamp01(0.6 + 0.2 * clamp01(edge_depth / edge_frac) + 0.2 * clamp01(rsi_extra))

    tp = round(mid, 8)
    signal = Signal(
        direction=direction,
        entry_zone=[round(low, 8), round(high, 8)],
        stop_loss=round(stop, 8),
        take_profit=[tp, tp],
        confidence=round(conf, 6),
        size_pct=size_for(conf),
        reasons=[
            f"mean-reversion {direction}: fade range "
            f"[{round(band_low, 2)}, {round(band_high, 2)}] → mid {round(mid, 2)} (rsi {round(rsi, 1)})"
        ],
        agent_scores={},
        risk_reward=round(rr, 6),
        alignment_pen=0.0,
        fvg_override=False,
        worst_case_fill=round(worst, 8),
        metadata={
            "strategy": "mean_reversion",
            "band_low": round(band_low, 8),
            "band_high": round(band_high, 8),
            "mid": tp,
        },
    )
    if log is not None:
        log.info(
            "mr_signal",
            direction=direction,
            band_low=round(band_low, 8),
            band_high=round(band_high, 8),
            mid=tp,
            rsi=round(rsi, 2),
            rr=round(rr, 4),
            conf=round(conf, 6),
        )
    return signal


def _skip(log, reason: str, **extra) -> None:
    if log is not None:
        log.info("mr_skipped", reason=reason, **extra)
