"""Deterministic interpreted context for planner envelopes."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ats.config import settings
from ats.engine import state
from ats.learning.fingerprint import build_fingerprint
from ats.learning.retrieval import retrieve_memory_summary
from ats.logging import get_logger

log = get_logger(__name__)


def _f(value: Any) -> float | None:
    try:
        f = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return None if f is None or math.isnan(f) else f


def _price(feature_row: dict[str, Any], candles: list[dict[str, Any]]) -> float | None:
    return _f(feature_row.get("price")) or _f(feature_row.get("close")) or (
        _f(candles[-1].get("close")) if candles else None
    )


def _distance_atr(a: float | None, b: float | None, atr: float | None) -> float | None:
    if a is None or b is None or atr is None or atr <= 0:
        return None
    return round(abs(a - b) / atr, 6)


def _signed_distance_atr(
    price: float | None, level: float | None, atr: float | None
) -> float | None:
    if price is None or level is None or atr is None or atr <= 0:
        return None
    return round((price - level) / atr, 6)


def _pivots(candles: list[dict[str, Any]], wing: int = 2) -> tuple[list[float], list[float]]:
    highs: list[float] = []
    lows: list[float] = []
    if len(candles) < wing * 2 + 1:
        return highs, lows
    for i in range(wing, len(candles) - wing):
        high = _f(candles[i].get("high"))
        low = _f(candles[i].get("low"))
        if high is None or low is None:
            continue
        left = candles[i - wing : i]
        right = candles[i + 1 : i + wing + 1]
        left_highs = [_f(c.get("high")) for c in left]
        right_highs = [_f(c.get("high")) for c in right]
        left_lows = [_f(c.get("low")) for c in left]
        right_lows = [_f(c.get("low")) for c in right]
        if all(v is not None and high > v for v in [*left_highs, *right_highs]):
            highs.append(high)
        if all(v is not None and low < v for v in [*left_lows, *right_lows]):
            lows.append(low)
    return highs, lows


def _nearest_above(price: float | None, levels: list[float]) -> float | None:
    if price is None:
        return None
    above = [x for x in levels if x > price]
    return min(above) if above else None


def _nearest_below(price: float | None, levels: list[float]) -> float | None:
    if price is None:
        return None
    below = [x for x in levels if x < price]
    return max(below) if below else None


def _slope_label(values: list[float], *, flat_threshold: float = 0.01) -> str:
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return "flat"
    delta = clean[-1] - clean[0]
    if delta > flat_threshold:
        return "rising"
    if delta < -flat_threshold:
        return "falling"
    return "flat"


def _sign(value: float | None) -> int:
    if value is None:
        return 0
    return 1 if value > 0 else -1 if value < 0 else 0


def build_structure_context(
    candles: list[dict[str, Any]],
    feature_row: dict[str, Any],
) -> dict[str, Any]:
    """Summarise actionable price structure from recent candles."""
    window = candles[-96:] if len(candles) > 96 else candles
    price = _price(feature_row, window)
    atr = _f(feature_row.get("atr_14"))
    highs = [_f(c.get("high")) for c in window]
    lows = [_f(c.get("low")) for c in window]
    highs_f = [x for x in highs if x is not None]
    lows_f = [x for x in lows if x is not None]
    range_high = max(highs_f) if highs_f else None
    range_low = min(lows_f) if lows_f else None
    range_mid = (
        (range_high + range_low) / 2.0
        if range_high is not None and range_low is not None
        else None
    )
    pivot_highs, pivot_lows = _pivots(window)
    resistance_levels = pivot_highs + ([range_high] if range_high is not None else [])
    support_levels = pivot_lows + ([range_low] if range_low is not None else [])
    nearest_resistance = _nearest_above(price, resistance_levels)
    nearest_support = _nearest_below(price, support_levels)

    price_location = None
    if price is not None and range_high is not None and range_low is not None:
        span = range_high - range_low
        if span > 0:
            pos = (price - range_low) / span
            if pos < 0.33:
                price_location = "lower_range"
            elif pos > 0.66:
                price_location = "upper_range"
            else:
                price_location = "mid_range"

    return {
        "price_location": price_location,
        "range_high": range_high,
        "range_low": range_low,
        "range_mid": range_mid,
        "nearest_resistance": nearest_resistance,
        "nearest_support": nearest_support,
        "distance_to_resistance_atr": _distance_atr(nearest_resistance, price, atr),
        "distance_to_support_atr": _distance_atr(price, nearest_support, atr),
        "last_swing_high": pivot_highs[-1] if pivot_highs else range_high,
        "last_swing_low": pivot_lows[-1] if pivot_lows else range_low,
    }


def _relation_age(rows: list[dict[str, Any]], key: str) -> int | None:
    if not rows:
        return None
    current_close = _f(rows[-1].get("close")) or _f(rows[-1].get("price"))
    current_level = _f(rows[-1].get(key))
    if current_close is None or current_level is None:
        return None
    current = _sign(current_close - current_level)
    if current == 0:
        return 0
    age = 0
    for row in reversed(rows):
        close = _f(row.get("close")) or _f(row.get("price"))
        level = _f(row.get(key))
        if close is None or level is None or _sign(close - level) != current:
            break
        age += 1
    return age


def _impulse_age(rows: list[dict[str, Any]]) -> int | None:
    if len(rows) < 2:
        return None
    closes = [_f(r.get("close")) or _f(r.get("price")) for r in rows]
    if closes[-1] is None or closes[-2] is None:
        return None
    current = _sign(closes[-1] - closes[-2])
    if current == 0:
        return 0
    age = 1
    for i in range(len(closes) - 2, 0, -1):
        if closes[i] is None or closes[i - 1] is None:
            break
        if _sign(closes[i] - closes[i - 1]) != current:
            break
        age += 1
    return age


def _htf_rsi_state(higher_timeframes: dict[str, Any]) -> str:
    priority = ("4h", "1h")
    for tf in priority:
        rsi = _f(((higher_timeframes.get(tf) or {}).get("features") or {}).get("rsi_14"))
        if rsi is None:
            continue
        if rsi < 30:
            return f"{tf}_oversold"
        if rsi > 70:
            return f"{tf}_overbought"
    return "neutral"


def _htf_trend(higher_timeframes: dict[str, Any]) -> str | None:
    """Dominant trend from the most-recent CLOSED 4h (then 1h) bar: price vs EMA50.

    The 4h EMA50 is ~8 days of context, so its side is a stable macro-trend read that the
    15m agents (momentum-myopic) routinely fight: they kept voting long on intraday bounces
    inside a 4h downtrend — the dominant BTC-replay loss bucket. Returns ``"up"`` when price
    is above the slow EMA, ``"down"`` below, ``None`` only when neither chart has an EMA50
    yet (early in a replay window). The plan-time gate makes this the primary direction.
    """
    for tf in ("4h", "1h"):
        feats = ((higher_timeframes.get(tf) or {}).get("features")) or {}
        close = _f(feats.get("close")) or _f(feats.get("price"))
        ema50 = _f(feats.get("ema_50"))
        if close is None or ema50 is None:
            continue
        # Prefer the slow EMA stack (ema_50 vs ema_200): in a sustained downtrend the stack
        # stays bearish through intraday bounces, whereas close-vs-ema_50 flips to "up" on every
        # pop above the fast EMA and leaked counter-trend longs into the fade (the dominant loss
        # bucket). Fall back to close-vs-ema_50 only when ema_200 isn't warm yet.
        ema200 = _f(feats.get("ema_200"))
        if ema200 is not None:
            # Slow EMA stack (ema_50 vs ema_200): stays bearish through intraday bounces so the
            # gate blocks counter-trend longs across the whole established downtrend. (Adding a
            # `close >= ema_200` term was tried and regressed: it unblocks shorts too early in the
            # choppy top, where they whipsaw — iter5 shorts +36% → -16%. Reverted.)
            return "up" if ema50 >= ema200 else "down"
        return "up" if close > ema50 else "down"
    return None


def _htf_trend_strength(higher_timeframes: dict[str, Any]) -> float | None:
    """Normalized 4h (then 1h) slow-EMA-stack separation — a causal trend-strength proxy.

    Returns ``|ema_50 - ema_200| / ema_200`` from the most-recent CLOSED higher-tf bar: ~0 when
    the stack is flat/coiled (choppy drift, no established trend), rising as the trend extends and
    the EMAs fan apart. Magnitude only (direction is handled by :func:`_htf_trend`). ``None`` when
    neither chart has a warm ema_200 yet. Used by the swing trend-strength entry gate to stand
    aside in weak trends. Purely a function of closed higher-tf features — no look-ahead.
    """
    for tf in ("4h", "1h"):
        feats = ((higher_timeframes.get(tf) or {}).get("features")) or {}
        ema50 = _f(feats.get("ema_50"))
        ema200 = _f(feats.get("ema_200"))
        if ema50 is None or ema200 is None or ema200 == 0:
            continue
        return abs(ema50 - ema200) / abs(ema200)
    return None


def _htf_momentum(higher_timeframes: dict[str, Any]) -> float | None:
    """Signed higher-tf momentum in [-0.5, +0.5] — ``momentum_composite - 0.5`` (4h, then 1h).

    ``momentum_composite`` is a symbol-agnostic [0,1] blend (0.5 neutral), so the recentred value
    is directly comparable across coins: > 0 = bullish drive, < 0 = bearish. Used by the swing
    trend-strength gate's momentum escape to spare an early-but-accelerating trend. Causal (a
    closed higher-tf feature). ``None`` when neither chart has it yet.
    """
    for tf in ("4h", "1h"):
        feats = ((higher_timeframes.get(tf) or {}).get("features")) or {}
        mc = _f(feats.get("momentum_composite"))
        if mc is not None:
            return mc - 0.5
    return None


def preferred_direction(
    regime_cell: str | None,
    exhaustion_ctx: dict[str, Any],
    structure_ctx: dict[str, Any],
) -> str | None:
    """Deterministic soft directional steer (#4) from regime + exhaustion + structure.

    Returns ``"long"``/``"short"`` when the deterministic context favors a side, else
    ``None`` (ambiguous — let the strategist use the full ``allowed_directions`` gate).
    Mirrors the gate's logic: trend regimes steer with-trend, unless a higher timeframe is
    RSI-exhausted against the trend AND the base RSI/momentum slopes have turned (→ counter-
    trend mean reversion). Sideways regimes fade the range extreme.
    """
    head = (regime_cell or "").split("-", 1)[0].lower()
    htf_state = str(exhaustion_ctx.get("htf_rsi_state") or "neutral")
    rsi_slope = exhaustion_ctx.get("base_rsi_slope")
    mom_slope = exhaustion_ctx.get("momentum_slope")

    if head == "bull":
        if (
            htf_state.endswith("overbought")
            and rsi_slope == "falling"
            and mom_slope in {"falling", "flat"}
        ):
            return "short"
        return "long"
    if head == "bear":
        if (
            htf_state.endswith("oversold")
            and rsi_slope == "rising"
            and mom_slope in {"rising", "flat"}
        ):
            return "long"
        return "short"
    if head == "side":
        loc = structure_ctx.get("price_location")
        if loc == "upper_range":
            return "short"
        if loc == "lower_range":
            return "long"
    return None


def build_exhaustion_context(
    recent_features: list[dict[str, Any]],
    feature_row: dict[str, Any],
    higher_timeframes: dict[str, Any],
) -> dict[str, Any]:
    rows = recent_features or [feature_row]
    price = _f(feature_row.get("price")) or _f(feature_row.get("close"))
    atr = _f(feature_row.get("atr_14"))
    rsi_slope = _slope_label(
        [_f(r.get("rsi_14")) for r in rows[-5:]],
        flat_threshold=0.25,
    )
    momentum_slope = _slope_label(
        [_f(r.get("momentum_composite")) for r in rows[-5:]],
        flat_threshold=0.01,
    )
    htf_state = _htf_rsi_state(higher_timeframes)
    ema20_dist = _signed_distance_atr(price, _f(feature_row.get("ema_20")), atr)
    ema50_dist = _signed_distance_atr(price, _f(feature_row.get("ema_50")), atr)

    oversold_squeeze = (
        htf_state.endswith("oversold")
        and rsi_slope == "rising"
        and momentum_slope in {"rising", "flat"}
        and (
            ema20_dist is not None
            and ema20_dist < -1.0
            or ema50_dist is not None
            and ema50_dist < -1.0
        )
    )
    overbought_squeeze = (
        htf_state.endswith("overbought")
        and rsi_slope == "falling"
        and momentum_slope in {"falling", "flat"}
        and (
            ema20_dist is not None
            and ema20_dist > 1.0
            or ema50_dist is not None
            and ema50_dist > 1.0
        )
    )
    squeeze_risk = "high" if oversold_squeeze or overbought_squeeze else "normal"

    return {
        "trend_age_bars": _relation_age(rows, "ema_20"),
        "impulse_age_bars": _impulse_age(rows),
        "distance_from_ema20_atr": ema20_dist,
        "distance_from_ema50_atr": ema50_dist,
        "base_rsi_slope": rsi_slope,
        "momentum_slope": momentum_slope,
        "htf_rsi_state": htf_state,
        "squeeze_risk": squeeze_risk,
    }


def _price_slope_10(candles: list[dict[str, Any]]) -> float | None:
    if len(candles) < 11:
        return None
    start = _f(candles[-11].get("close"))
    end = _f(candles[-1].get("close"))
    if start is None or end is None:
        return None
    return end - start


def build_volume_context(
    candles: list[dict[str, Any]],
    feature_row: dict[str, Any],
) -> dict[str, Any]:
    current = candles[-1] if candles else feature_row
    volume = _f(current.get("volume")) or _f(feature_row.get("volume"))
    prev_vols = [_f(c.get("volume")) for c in candles[-21:-1]]
    prev_vols_f = [v for v in prev_vols if v is not None]
    avg_vol = sum(prev_vols_f) / len(prev_vols_f) if prev_vols_f else None
    relative_volume = volume / avg_vol if volume is not None and avg_vol and avg_vol > 0 else None
    taker_buy = _f(current.get("taker_buy_vol")) or _f(feature_row.get("taker_buy_vol"))
    taker_buy_ratio = (
        taker_buy / volume
        if taker_buy is not None and volume is not None and volume > 0
        else None
    )
    price_slope = _price_slope_10(candles)
    cvd_slope = _f(feature_row.get("cvd_slope_10"))
    price_sign = _sign(price_slope)
    cvd_sign = _sign(cvd_slope)
    cvd_agrees = None if price_sign == 0 or cvd_sign == 0 else price_sign == cvd_sign
    vol_z = _f(feature_row.get("vol_zscore_20"))

    quality = "neutral"
    if vol_z is not None and cvd_agrees is not None:
        if vol_z > 1.0 and cvd_agrees:
            quality = "strong"
        elif vol_z <= 0.0 or not cvd_agrees:
            quality = "weak"

    return {
        "relative_volume_20": round(relative_volume, 6) if relative_volume is not None else None,
        "taker_buy_ratio": round(taker_buy_ratio, 6) if taker_buy_ratio is not None else None,
        "cvd_agrees_with_price": cvd_agrees,
        "breakout_volume_quality": quality,
    }


def _empty_memory_summary() -> dict[str, Any]:
    return {
        "similar_count": 0,
        "win_rate": None,
        "avg_pnl_pct": None,
        "expectancy_pct": None,
        "top_failure": None,
        "lesson": None,
        "confidence": "low",
    }


async def build_planner_context(
    session: AsyncSession,
    symbol: str,
    tf: str,
    feature_row: dict[str, Any],
    *,
    as_of: datetime,
    regime: dict[str, Any] | None = None,
    recent_candles: list[dict[str, Any]] | None = None,
    higher_timeframes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build compact interpreted context for create_plan without changing plan output."""
    candles = recent_candles or []
    htf = higher_timeframes or {}
    try:
        recent_features = await state.recent_feature_rows(session, symbol, tf, as_of, n=50)
    except Exception as exc:  # noqa: BLE001 - context is advisory; planning should survive.
        log.warning("planner_context_recent_features_failed", symbol=symbol, error=str(exc))
        recent_features = [feature_row]

    memory_summary = _empty_memory_summary()
    if settings.memory_enabled:
        try:
            fp = build_fingerprint(feature_row, regime)
            memory_summary = await retrieve_memory_summary(session, fp)
        except Exception as exc:  # noqa: BLE001 - memory is advisory.
            log.warning("planner_context_memory_failed", symbol=symbol, error=str(exc))

    return {
        "structure": build_structure_context(candles, feature_row),
        "exhaustion": build_exhaustion_context(recent_features, feature_row, htf),
        "volume_context": build_volume_context(candles, feature_row),
        "memory_summary": memory_summary,
    }
