"""Deterministic risk manager — the safety layer before execution.

Runs after a setup is detected and confirmed. Enforces hard constraints and may
trim size. Pure (no DB): callers pass the current open positions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Liquidation buffer: require the stop to sit inside LIQ_SAFETY of the raw isolated-margin
# liquidation distance (1/leverage). < 1.0 so we reject before the true liquidation level,
# leaving headroom for fees/maintenance margin we don't model.
LIQ_SAFETY = 0.9


@dataclass
class RiskSizing:
    """Output of risk-based sizing. ``size_pct`` is notional / equity (== leverage)."""

    ok: bool
    size_pct: float
    leverage: float
    notional_usd: float
    risk_usd: float
    liq_price: float | None = None
    reason: str = ""


@dataclass
class RiskDecision:
    approved: bool
    size_pct: float  # notional / equity (>= 1.0 means leveraged)
    reasons: list[str] = field(default_factory=list)
    leverage: float = 1.0
    risk_usd: float = 0.0
    liq_price: float | None = None
    notional_usd: float = 0.0


def size_for_risk(
    direction: str,
    entry: float,
    stop: float,
    *,
    equity_usd: float,
    risk_pct: float,
    max_leverage: float,
) -> RiskSizing:
    """Size a position so a stop-out loses ~``risk_pct`` of equity, capped by ``max_leverage``.

    Returns the notional (as a fraction of equity = leverage), the dollars actually at risk
    (always <= the risk budget), and the isolated-margin liquidation price. ``ok`` is False
    when the stop is degenerate or would sit beyond the liquidation level for the leverage used.
    """
    if entry <= 0:
        return RiskSizing(False, 0.0, 0.0, 0.0, 0.0, reason="entry price non-positive")
    stop_dist = abs(entry - stop) / entry
    if stop_dist <= 0:
        return RiskSizing(False, 0.0, 0.0, 0.0, 0.0, reason="stop at entry (zero distance)")
    if risk_pct <= 0 or equity_usd <= 0 or max_leverage <= 0:
        return RiskSizing(False, 0.0, 0.0, 0.0, 0.0, reason="non-positive risk/equity/leverage")

    risk_budget = equity_usd * risk_pct
    notional = min(risk_budget / stop_dist, equity_usd * max_leverage)
    leverage = notional / equity_usd
    size_pct = leverage  # notional fraction of equity
    risk_usd = notional * stop_dist  # <= risk_budget by construction

    # Liquidation only exists when notional exceeds equity (leverage > 1). At <= 1x the
    # position is fully collateralised and can never be liquidated.
    liq_price: float | None = None
    if leverage > 1.0:
        liq_dist = LIQ_SAFETY / leverage
        if stop_dist >= liq_dist:
            return RiskSizing(
                False, 0.0, 0.0, 0.0, 0.0,
                reason=f"stop {stop_dist:.4f} beyond liquidation {liq_dist:.4f} at {leverage:.2f}x",
            )
        liq_price = entry * (1 - liq_dist) if direction == "long" else entry * (1 + liq_dist)
    return RiskSizing(True, size_pct, leverage, notional, risk_usd, liq_price)


def regime_allows(regime_cell: str | None, direction: str) -> bool:
    """Whether a setup's direction is aligned with the current regime.

    The losing bucket in replay was counter-trend entries in chop. Rule of thumb:
    - sideways regimes ("side-*"): take nothing (range chop stops out both ways),
    - bull regimes ("bull-*"): longs only,
    - bear regimes ("bear-*"): shorts only,
    - unknown / missing regime: allow (no information to gate on).
    """
    if not regime_cell:
        return True
    head = regime_cell.split("-", 1)[0].lower()
    if head == "side":
        return False
    if head == "bull":
        return direction == "long"
    if head == "bear":
        return direction == "short"
    return True


def reward_risk(direction: str, entry: float, stop: float, take_profit: list[float]) -> float:
    """Reward:risk to the FIRST take-profit. Returns 0.0 if risk is non-positive."""
    if not take_profit:
        return 0.0
    tp1 = float(take_profit[0])
    if direction == "long":
        risk = entry - stop
        reward = tp1 - entry
    else:  # short
        risk = stop - entry
        reward = entry - tp1
    if risk <= 0:
        return 0.0
    return reward / risk


def assess(
    setup: dict[str, Any],
    *,
    price: float,
    open_positions: list[dict[str, Any]],
    equity_usd: float,
    risk_per_trade_pct: float,
    max_leverage: float,
    min_rr: float,
    size_multiplier: float = 1.0,
    atr: float | None = None,
    min_stop_atr_mult: float = 0.0,
) -> RiskDecision:
    """Approve/reject a detected+confirmed setup and size it by risk.

    Constraints: one open position per symbol, minimum reward:risk, and risk-based sizing
    that loses at most ``risk_per_trade_pct`` of equity on a stop-out (leverage capped by
    ``max_leverage``, stop must sit inside the liquidation level). ``size_multiplier`` (e.g.
    a confirm REDUCE_SIZE, <= 1) only ever scales the risk budget down. ``setup`` is a dict
    with direction/entry/stop/take_profit/symbol.

    ``atr`` + ``min_stop_atr_mult``: when both are supplied and ``min_stop_atr_mult > 0``,
    rejects setups whose stop distance (in price points) is smaller than
    ``min_stop_atr_mult * atr``. This prevents noise-stops — stops so tight they sit inside
    normal bar wiggle and get swept before price has a chance to reach the target.
    """
    reasons: list[str] = []
    symbol = setup.get("symbol")

    if any(p.get("symbol") == symbol for p in open_positions):
        return RiskDecision(False, 0.0, [f"already have an open position in {symbol}"])

    rr = reward_risk(
        setup["direction"], price, float(setup["stop_loss"]), list(setup["take_profit"])
    )
    if rr < min_rr:
        return RiskDecision(False, 0.0, [f"reward:risk {rr:.2f} below min {min_rr:.2f}"])

    if atr is not None and atr > 0 and min_stop_atr_mult > 0:
        stop_dist = abs(price - float(setup["stop_loss"]))
        min_dist = min_stop_atr_mult * atr
        if stop_dist < min_dist:
            return RiskDecision(
                False, 0.0,
                [f"stop {stop_dist:.1f}pts < {min_stop_atr_mult}x ATR ({min_dist:.1f}pts) — noise-stop"],
            )

    # Fixed cap + scaling: the multiplier (<= 1) trims risk, never raises it past the cap.
    eff_risk_pct = min(risk_per_trade_pct * float(size_multiplier), risk_per_trade_pct)
    sizing = size_for_risk(
        setup["direction"], price, float(setup["stop_loss"]),
        equity_usd=equity_usd, risk_pct=eff_risk_pct, max_leverage=max_leverage,
    )
    if not sizing.ok or sizing.size_pct <= 0:
        return RiskDecision(False, 0.0, [sizing.reason or "size resolved to zero"])

    if size_multiplier != 1.0:
        reasons.append(f"risk x{size_multiplier:.2f} (confirm)")
    reasons.append(
        f"rr {rr:.2f}; lev {sizing.leverage:.2f}x; risk ${sizing.risk_usd:.2f}"
    )
    return RiskDecision(
        True,
        sizing.size_pct,
        reasons,
        leverage=sizing.leverage,
        risk_usd=sizing.risk_usd,
        liq_price=sizing.liq_price,
        notional_usd=sizing.notional_usd,
    )
