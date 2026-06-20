"""Direction-specific SL/TP rules and the default entry band.

The default (non-FVG) entry is a narrow ``close ± band`` zone; the synthesizer replaces
it with the FVG zone when PriceAction qualifies. The stop sits ``min_stop_atr_mult``
ATR beyond the *worst-case fill* (the zone edge nearest the take-profit) so the
deterministic noise-stop guard in the risk layer accepts it. TP1 is the larger of the
RR-target distance and ">2 round-trip costs", so the scale-out leg is net positive even
on a fee-only scratch.
"""

from __future__ import annotations

from dataclasses import dataclass

# Default entry band half-width as a fraction of ATR (narrow — scalper entries).
_BAND_ATR_FRAC = 0.25
# TP1 distance as an ATR multiple. Version-controlled like the weights (anti-tuning): TP
# is a *structural* target, so realized RR is emergent and the synthesizer's RR floor can
# genuinely reject a setup whose stop/target geometry doesn't clear it.
_REWARD_ATR_MULT = 3.0


@dataclass
class Levels:
    entry_zone: list[float]  # [low, high]
    stop_loss: float
    take_profit: list[float]  # [tp1, tp2]
    worst_case_fill: float
    risk_reward: float


def _round_trip_cost_frac(fee_bps: float, slippage_bps: float) -> float:
    """Entry+exit cost as a fraction of notional (both legs, fee + slippage)."""
    return 2.0 * (fee_bps + slippage_bps) / 10_000.0


def default_band(close: float, atr: float) -> list[float]:
    """The non-FVG entry zone: a narrow symmetric band around the current close."""
    half = _BAND_ATR_FRAC * atr
    return [round(close - half, 8), round(close + half, 8)]


def compute_levels(
    direction: str,
    *,
    close: float,
    atr: float,
    entry_zone: list[float],
    min_stop_atr_mult: float,
    fee_bps: float,
    slippage_bps: float,
) -> Levels:
    """Build stop/targets for ``entry_zone`` and report RR at the worst-case fill.

    Both stop and TP1 are fixed ATR-multiple (structural) distances from the worst-case
    fill, so realized RR is emergent — the synthesizer's RR floor can reject it. TP1 also
    clears ">2 round-trip costs" so the scale-out leg is net positive; TP2 extends to
    ~1.7× the TP1 distance. RR is computed at the worst-case fill so it matches the
    engine's admissibility recheck (``_worst_case_fill`` / ``_admissible_setups``).
    """
    low, high = entry_zone
    stop_atr = max(min_stop_atr_mult, 1.0)
    cost_floor = 2.0 * _round_trip_cost_frac(fee_bps, slippage_bps)

    if direction == "long":
        worst = high  # filled at the top of the band → least favorable for a long
        stop = worst - stop_atr * atr
        risk = worst - stop
        tp_dist = max(_REWARD_ATR_MULT * atr, cost_floor * worst)
        tp1 = worst + tp_dist
        tp2 = worst + 1.7 * tp_dist
    else:  # short
        worst = low  # filled at the bottom of the band → least favorable for a short
        stop = worst + stop_atr * atr
        risk = stop - worst
        tp_dist = max(_REWARD_ATR_MULT * atr, cost_floor * worst)
        tp1 = worst - tp_dist
        tp2 = worst - 1.7 * tp_dist

    rr = abs(tp1 - worst) / risk if risk > 0 else 0.0
    return Levels(
        entry_zone=[round(low, 8), round(high, 8)],
        stop_loss=round(stop, 8),
        take_profit=[round(tp1, 8), round(tp2, 8)],
        worst_case_fill=round(worst, 8),
        risk_reward=round(rr, 6),
    )
