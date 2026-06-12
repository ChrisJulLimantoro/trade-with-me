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
    """Output of margin-aware risk sizing.

    ``size_pct`` remains notional / equity for backwards compatibility. ``leverage`` is
    isolated leverage over ``margin_usd`` and is never below 1.0.
    """

    ok: bool
    size_pct: float
    leverage: float
    margin_usd: float
    notional_usd: float
    risk_usd: float
    liq_price: float | None = None
    reason: str = ""


@dataclass
class RiskDecision:
    approved: bool
    size_pct: float  # notional / equity
    reasons: list[str] = field(default_factory=list)
    leverage: float = 1.0
    margin_usd: float = 0.0
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
    max_margin_pct_per_trade: float = 0.20,
    remaining_margin_usd: float | None = None,
    remaining_risk_usd: float | None = None,
) -> RiskSizing:
    """Size a position by stop risk, isolated margin cap, and remaining portfolio heat.

    Returns notional, margin, leverage, dollars at risk, and liquidation price. ``ok`` is
    False when inputs are degenerate or no margin/risk budget remains.
    """
    if entry <= 0:
        return RiskSizing(False, 0.0, 0.0, 0.0, 0.0, 0.0, reason="entry price non-positive")
    stop_dist = abs(entry - stop) / entry
    if stop_dist <= 0:
        return RiskSizing(False, 0.0, 0.0, 0.0, 0.0, 0.0, reason="stop at entry (zero distance)")
    if (
        risk_pct <= 0
        or equity_usd <= 0
        or max_leverage <= 0
        or max_margin_pct_per_trade <= 0
    ):
        return RiskSizing(
            False, 0.0, 0.0, 0.0, 0.0, 0.0,
            reason="non-positive risk/equity/leverage/margin",
        )

    risk_budget = equity_usd * risk_pct
    if remaining_risk_usd is not None:
        risk_budget = min(risk_budget, remaining_risk_usd)
    if risk_budget <= 0:
        return RiskSizing(False, 0.0, 0.0, 0.0, 0.0, 0.0, reason="portfolio risk exhausted")

    margin_limit = equity_usd * max_margin_pct_per_trade
    if remaining_margin_usd is not None:
        margin_limit = min(margin_limit, remaining_margin_usd)
    if margin_limit <= 0:
        return RiskSizing(False, 0.0, 0.0, 0.0, 0.0, 0.0, reason="portfolio margin exhausted")

    notional = min(risk_budget / stop_dist, margin_limit * max_leverage)
    if notional <= 0:
        return RiskSizing(False, 0.0, 0.0, 0.0, 0.0, 0.0, reason="size resolved to zero")

    margin_usd = min(notional, margin_limit)
    leverage = max(1.0, notional / margin_usd)

    # If the isolated leverage would put the stop outside the safety-adjusted liquidation
    # distance, cap notional to the largest safe leveraged exposure. If that falls below
    # 1x, use unlevered notional/margin instead.
    if leverage > 1.0:
        safe_leverage = LIQ_SAFETY / stop_dist
        if safe_leverage <= 1.0:
            notional = min(notional, margin_limit)
            margin_usd = notional
            leverage = 1.0
        elif leverage >= safe_leverage:
            notional = margin_limit * safe_leverage * 0.999
            margin_usd = min(notional, margin_limit)
            leverage = max(1.0, notional / margin_usd)

    size_pct = notional / equity_usd  # notional fraction of equity
    risk_usd = notional * stop_dist  # <= risk_budget by construction

    liq_price: float | None = None
    if leverage > 1.0:
        liq_dist = LIQ_SAFETY / leverage
        liq_price = entry * (1 - liq_dist) if direction == "long" else entry * (1 + liq_dist)
    return RiskSizing(True, size_pct, leverage, margin_usd, notional, risk_usd, liq_price)


def _open_margin_usd(position: dict[str, Any], equity_usd: float) -> float:
    if position.get("margin_usd") is not None:
        return float(position["margin_usd"])
    notional = position.get("notional_usd")
    lev = position.get("leverage")
    if notional is not None and lev is not None and float(lev) > 0:
        return float(notional) / float(lev)
    if position.get("size_pct") is not None:
        notional = equity_usd * float(position["size_pct"])
        if lev is not None and float(lev) > 0:
            return notional / float(lev)
        return min(notional, equity_usd)
    return 0.0


def _open_risk_usd(position: dict[str, Any]) -> float:
    return float(position.get("risk_usd") or 0.0)


def regime_allows(regime_cell: str | None, direction: str) -> bool:
    """Whether a setup's direction is aligned with the current regime.

    Rule of thumb:
    - sideways regimes ("side-*"): allow both directions for range/mean-reversion profiles,
    - bull regimes ("bull-*"): longs only,
    - bear regimes ("bear-*"): shorts only,
    - unknown / missing regime: allow (no information to gate on).
    """
    if not regime_cell:
        return True
    head = regime_cell.split("-", 1)[0].lower()
    if head == "side":
        return direction in {"long", "short"}
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
    max_margin_pct_per_trade: float = 0.20,
    max_total_margin_pct: float = 0.60,
    max_portfolio_risk_pct: float = 0.03,
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
                [
                    f"stop {stop_dist:.1f}pts < {min_stop_atr_mult}x ATR "
                    f"({min_dist:.1f}pts) — noise-stop"
                ],
            )

    # Fixed cap + scaling: the multiplier (<= 1) trims risk, never raises it past the cap.
    eff_risk_pct = min(risk_per_trade_pct * float(size_multiplier), risk_per_trade_pct)
    open_margin_usd = sum(_open_margin_usd(p, equity_usd) for p in open_positions)
    open_risk_usd = sum(_open_risk_usd(p) for p in open_positions)
    remaining_margin_usd = equity_usd * max_total_margin_pct - open_margin_usd
    remaining_risk_usd = equity_usd * max_portfolio_risk_pct - open_risk_usd
    sizing = size_for_risk(
        setup["direction"], price, float(setup["stop_loss"]),
        equity_usd=equity_usd,
        risk_pct=eff_risk_pct,
        max_leverage=max_leverage,
        max_margin_pct_per_trade=max_margin_pct_per_trade,
        remaining_margin_usd=remaining_margin_usd,
        remaining_risk_usd=remaining_risk_usd,
    )
    if not sizing.ok or sizing.size_pct <= 0:
        return RiskDecision(False, 0.0, [sizing.reason or "size resolved to zero"])

    if size_multiplier != 1.0:
        reasons.append(f"risk x{size_multiplier:.2f} (confirm)")
    reasons.append(
        f"rr {rr:.2f}; margin ${sizing.margin_usd:.2f}; "
        f"notional ${sizing.notional_usd:.2f}; lev {sizing.leverage:.2f}x; "
        f"risk ${sizing.risk_usd:.2f}"
    )
    return RiskDecision(
        True,
        sizing.size_pct,
        reasons,
        leverage=sizing.leverage,
        margin_usd=sizing.margin_usd,
        risk_usd=sizing.risk_usd,
        liq_price=sizing.liq_price,
        notional_usd=sizing.notional_usd,
    )
