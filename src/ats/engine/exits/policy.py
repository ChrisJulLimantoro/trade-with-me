"""Exit policy — the concrete exit knobs a trade carries after entry.

Resolves the regime-appropriate :class:`ExitPolicy` (trend vs. range posture), serialises
it onto trade metadata, and reloads it for an open trade. Also the two risk-only clamps the
observer's adjustments pass through. Pure functions of ``settings`` + the trade metadata —
no DB, no I/O — so the exit manager and observer can share one source of exit truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ats.config import settings


@dataclass(frozen=True)
class ExitPolicy:
    """Concrete exit knobs carried by a trade after entry."""

    regime_cell: str | None
    exit_mode: str
    scale_out_frac: float
    trail_atr_mult: float
    breakeven_requires_tp1: bool
    trail_after_tp1_only: bool
    early_stop_mode: str
    early_trail_arm_atr: float
    early_trail_atr_mult: float


def is_sideways(regime_cell: str | None) -> bool:
    return bool(regime_cell and regime_cell.split("-", 1)[0].lower() == "side")


def exit_policy_for_regime(regime_cell: str | None) -> ExitPolicy:
    if is_sideways(regime_cell) and settings.exits.sideways_exit_mode == "range":
        return ExitPolicy(
            regime_cell=regime_cell,
            exit_mode="range",
            scale_out_frac=settings.exits.sideways_scale_out_frac,
            trail_atr_mult=settings.exits.sideways_trail_atr_mult,
            breakeven_requires_tp1=settings.exits.sideways_breakeven_requires_tp1,
            trail_after_tp1_only=settings.exits.sideways_trail_after_tp1_only,
            early_stop_mode=settings.exits.sideways_early_stop_mode,
            early_trail_arm_atr=settings.exits.sideways_early_trail_arm_atr,
            early_trail_atr_mult=settings.exits.sideways_early_trail_atr_mult,
        )
    return ExitPolicy(
        regime_cell=regime_cell,
        exit_mode="trend",
        scale_out_frac=settings.exits.scale_out_frac,
        trail_atr_mult=settings.exits.trail_atr_mult,
        breakeven_requires_tp1=False,
        trail_after_tp1_only=False,
        early_stop_mode=settings.exits.early_stop_mode,
        early_trail_arm_atr=settings.exits.early_trail_arm_atr,
        early_trail_atr_mult=settings.exits.early_trail_atr_mult,
    )


def exit_policy_metadata(policy: ExitPolicy) -> dict[str, Any]:
    return {
        "regime_cell": policy.regime_cell,
        "exit_mode": policy.exit_mode,
        "exit_scale_out_frac": policy.scale_out_frac,
        "exit_trail_atr_mult": policy.trail_atr_mult,
        "exit_breakeven_requires_tp1": policy.breakeven_requires_tp1,
        "exit_trail_after_tp1_only": policy.trail_after_tp1_only,
        "exit_early_stop_mode": policy.early_stop_mode,
        "exit_early_trail_arm_atr": policy.early_trail_arm_atr,
        "exit_early_trail_atr_mult": policy.early_trail_atr_mult,
    }


def exit_policy_from_metadata(md: dict[str, Any] | None) -> ExitPolicy:
    md = md or {}
    mode = md.get("exit_mode")
    if mode in {"range", "trend"}:
        return ExitPolicy(
            regime_cell=md.get("regime_cell"),
            exit_mode=str(mode),
            scale_out_frac=float(md.get("exit_scale_out_frac", settings.exits.scale_out_frac)),
            trail_atr_mult=float(md.get("exit_trail_atr_mult", settings.exits.trail_atr_mult)),
            breakeven_requires_tp1=bool(md.get("exit_breakeven_requires_tp1", False)),
            trail_after_tp1_only=bool(md.get("exit_trail_after_tp1_only", False)),
            early_stop_mode=str(md.get("exit_early_stop_mode", settings.exits.early_stop_mode)),
            early_trail_arm_atr=float(
                md.get("exit_early_trail_arm_atr", settings.exits.early_trail_arm_atr)
            ),
            early_trail_atr_mult=float(
                md.get("exit_early_trail_atr_mult", settings.exits.early_trail_atr_mult)
            ),
        )
    regime_cell = md.get("regime_cell")
    return exit_policy_for_regime(str(regime_cell) if regime_cell else None)


def clamp_tightened_stop(direction: str, cur_stop: float, new_stop: float, price: float) -> float:
    """Clamp an observation's proposed stop so it can only tighten, never widen risk.

    The stop may move toward price but never away from it and never past the current price
    (which would be an instant, pathological fill). Returns the clamped working stop.
    """
    if direction == "long":
        return min(max(cur_stop, new_stop), price)
    return max(min(cur_stop, new_stop), price)


def clamp_extended_tp(direction: str, cur_final_tp: float, new_tp: float) -> float:
    """Clamp a proposed take-profit so the final target can only extend outward."""
    return max(cur_final_tp, new_tp) if direction == "long" else min(cur_final_tp, new_tp)
