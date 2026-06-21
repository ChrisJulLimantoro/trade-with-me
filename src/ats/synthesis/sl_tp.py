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
# Pullback depth (ATR fraction) the entry sits on the *favorable* side of close: a short's
# zone is placed ABOVE close (limit-sell into a bounce), a long's BELOW (limit-buy a dip).
# This stops the detector from filling at the worst-case edge of a symmetric band right as
# price extends (selling the low / buying the high → the dominant knife-catch stop-out).
_PULLBACK_ATR_FRAC = 0.25
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


def default_band(close: float, atr: float, direction: str = "long") -> list[float]:
    """The non-FVG entry zone: a narrow band on the pullback side of the current close.

    For a ``short`` the band sits ABOVE close (fill = its low, ``close + pullback``) so the
    detector limit-sells into a bounce; for a ``long`` it sits BELOW close (fill = its high,
    ``close - pullback``) so it limit-buys a dip. The worst-case fill the engine uses is the
    edge nearest close, i.e. exactly ``pullback`` ATR away — a better price than a symmetric
    band's worst edge and far less prone to filling at the extreme of an impulse leg.
    """
    half = _BAND_ATR_FRAC * atr
    pull = _PULLBACK_ATR_FRAC * atr
    if direction == "short":
        near = close + pull          # fill (zone low) sits a pullback above close
        return [round(near, 8), round(near + 2 * half, 8)]
    near = close - pull              # long: fill (zone high) sits a pullback below close
    return [round(near - 2 * half, 8), round(near, 8)]


def compute_levels(
    direction: str,
    *,
    close: float,
    atr: float,
    entry_zone: list[float],
    min_stop_atr_mult: float,
    fee_bps: float,
    slippage_bps: float,
    reward_atr_mult: float = _REWARD_ATR_MULT,
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
        tp_dist = max(reward_atr_mult * atr, cost_floor * worst)
        tp1 = worst + tp_dist
        tp2 = worst + 1.7 * tp_dist
    else:  # short
        worst = low  # filled at the bottom of the band → least favorable for a short
        stop = worst + stop_atr * atr
        risk = stop - worst
        tp_dist = max(reward_atr_mult * atr, cost_floor * worst)
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
