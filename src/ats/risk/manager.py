"""Deterministic risk manager — the safety layer before execution.

Runs after a setup is detected and confirmed. Enforces hard constraints and may
trim size. Pure (no DB): callers pass the current open positions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RiskDecision:
    approved: bool
    size_pct: float
    reasons: list[str] = field(default_factory=list)


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
    max_position_pct: float,
    min_rr: float,
    size_multiplier: float = 1.0,
) -> RiskDecision:
    """Approve/trim/reject a detected+confirmed setup.

    Constraints: one open position per symbol, minimum reward:risk, and a hard size
    cap. ``size_multiplier`` (e.g. from a confirm REDUCE_SIZE) is applied before the
    cap. ``setup`` is a dict with direction/entry/stop/take_profit/size_pct/symbol.
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

    size = float(setup["size_pct"]) * float(size_multiplier)
    if size_multiplier != 1.0:
        reasons.append(f"size x{size_multiplier:.2f} (confirm)")
    if size > max_position_pct:
        reasons.append(f"size capped {size:.3f}->{max_position_pct:.3f}")
        size = max_position_pct
    if size <= 0:
        return RiskDecision(False, 0.0, ["size resolved to zero"])

    reasons.append(f"rr {rr:.2f}")
    return RiskDecision(True, size, reasons)
