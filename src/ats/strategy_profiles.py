"""Named strategy profiles — bundles of engine/risk knobs applied as a unit.

A profile is a set of overrides on the nested knob groups of :data:`ats.config.settings`
(``risk`` / ``exits`` / ``observer`` / ``plan``). Applying one mutates the live singleton in
place (every consumer reads ``settings.<group>.X`` at call time, so the override takes effect
for the rest of the process). This lets a single CLI flag swap the whole risk posture — e.g. a
tight, high-leverage *scalper* vs. the default *swing* style — without editing ``.env`` or
threading dozens of parameters through the call stack.

Add a profile by adding an entry to :data:`PROFILES`. Keys must be real group names and real
fields on that group; unknown keys raise so typos fail loudly.
"""

from __future__ import annotations

from typing import Any

from ats.config import Settings, settings

# Each profile maps group -> {field -> override}. Only the fields that differ from the
# defaults are listed; everything else keeps the baseline value.
PROFILES: dict[str, dict[str, dict[str, Any]]] = {
    # The shipped defaults — explicit so `--profile baseline` is a no-op you can name.
    "baseline": {},
    # Scalper: small equity, high leverage, tight stops and targets, short holds. With
    # tight (sub-ATR) stops the risk-based sizer naturally pushes leverage up toward the
    # higher cap while still risking only `risk_per_trade_pct` of equity on a stop-out.
    # Trades are small, fast moves: lower min-RR lets nearby targets qualify, the noise-stop
    # floor is relaxed so tight stops aren't rejected, and the hold/refresh windows shrink.
    "scalper": {
        "risk": {
            "paper_equity_usd": 1_000.0,   # smaller book
            "max_leverage": 20.0,           # conservative isolated leverage cap
            "risk_per_trade_pct": 0.025,    # still risk ~1% of (the smaller) equity per stop-out
            "max_margin_pct_per_trade": 0.20,
            "max_total_margin_pct": 0.60,
            "max_portfolio_risk_pct": 0.03,
            "min_rr": 1.0,                 # accept ~1:1 — scalps take quick, nearby targets
            "min_stop_atr_mult": 0.5,      # allow tight stops (relax the noise-stop guard)
        },
        "plan": {
            "max_setups_per_plan": 3,
            "plan_refresh_bars": 8,        # re-think the plan more often (~2h on 15m)
        },
        "exits": {
            "max_hold_bars": 8,            # ~2h on 15m; scalps don't marinate (>0 = time-stop ON)
            "trail_atr_mult": 1.0,         # trail closer to lock small gains fast
            "scale_out_frac": 0.5,         # bank half at the first target, ride the rest
        },
        "observer": {
            "observe_enabled": False,
            "observe_every_bars": 1,       # check the open trade every finer-tf bar
            # Part 2 Role 2: the exit-strategy observer must earn its calls. Gate the per-bar
            # LLM observation on deterministic thesis health (decaying/broken/stale) — the
            # deterministic exit machine (stop/TP/scale/trail/breakeven/time-stop) still runs
            # every bar regardless. Was 99.5% no-op at per-bar cadence.
            # "observe_only_on_health": False,
        },
    },
    # Scalper v2: the scalper posture with the deterministic-work levers turned on for A/B
    # against `scalper` (#1 entry confirmation, #4 preferred-direction hint, #5 event-driven
    # observer, #6 regime-change replan). Use the run-tagging harness to compare side by side.
    "scalper_v2": {
        "risk": {
            "paper_equity_usd": 1_000.0,
            "max_leverage": 20.0,
            "risk_per_trade_pct": 0.025,
            "max_margin_pct_per_trade": 0.20,
            "max_total_margin_pct": 0.60,
            "max_portfolio_risk_pct": 0.03,
            "min_rr": 1.0,
            "min_stop_atr_mult": 0.5,
        },
        "plan": {
            "max_setups_per_plan": 3,
            "plan_refresh_bars": 8,
            # The deterministic-work levers under test.
            "entry_confirmation_enabled": True,
            "deterministic_direction_hint": True,
            "replan_on_regime_change": True,
        },
        "exits": {
            "max_hold_bars": 8,            # ~2h on 15m; scalps don't marinate (>0 = time-stop ON)
            "trail_atr_mult": 1.0,
            "scale_out_frac": 0.5,
        },
        "observer": {
            "observe_every_bars": 1,
            "observe_only_on_health": True,
        },
    },
}


def apply_profile(name: str, target: Settings = settings) -> dict[str, Any]:
    """Apply a named profile's grouped overrides onto ``target`` (defaults to the singleton).

    Returns the applied overrides as a flat ``{"group.field": value}`` map plus
    ``strategy_profile`` (empty overrides for ``baseline``). Raises ``KeyError`` for an unknown
    profile and ``AttributeError`` for an unknown group or field.
    """
    if name not in PROFILES:
        raise KeyError(f"unknown profile '{name}'. Available: {', '.join(sorted(PROFILES))}")
    applied: dict[str, Any] = {}
    for group, overrides in PROFILES[name].items():
        if not hasattr(target, group):
            raise AttributeError(f"profile '{name}' sets unknown config group '{group}'")
        section = getattr(target, group)
        for field, value in overrides.items():
            if not hasattr(section, field):
                raise AttributeError(
                    f"profile '{name}' sets unknown field '{group}.{field}'"
                )
            setattr(section, field, value)
            applied[f"{group}.{field}"] = value
    target.strategy_profile = name
    return {**applied, "strategy_profile": name}
