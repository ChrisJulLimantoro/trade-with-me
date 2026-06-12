"""Named strategy profiles — bundles of engine/risk knobs applied as a unit.

A profile is just a set of overrides on :data:`ats.config.settings`. Applying one mutates
the live singleton in place (every consumer reads ``settings.X`` at call time, so the
override takes effect for the rest of the process). This lets a single CLI flag swap the
whole risk posture — e.g. a tight, high-leverage *scalper* vs. the default *swing* style —
without editing ``.env`` or threading dozens of parameters through the call stack.

Add a profile by adding an entry to :data:`PROFILES`. Keys must be real ``Settings``
fields; unknown keys raise so typos fail loudly.
"""

from __future__ import annotations

from typing import Any

from ats.config import Settings, settings

# Each profile maps Settings-field -> override value. Only the fields that differ from the
# defaults are listed; everything else keeps the baseline value.
PROFILES: dict[str, dict[str, Any]] = {
    # The shipped defaults — explicit so `--profile baseline` is a no-op you can name.
    "baseline": {},
    # Scalper: small equity, high leverage, tight stops and targets, short holds. With
    # tight (sub-ATR) stops the risk-based sizer naturally pushes leverage up toward the
    # higher cap while still risking only `risk_per_trade_pct` of equity on a stop-out.
    # Trades are small, fast moves: lower min-RR lets nearby targets qualify, the noise-stop
    # floor is relaxed so tight stops aren't rejected, and the hold/refresh windows shrink.
    "scalper": {
        "paper_equity_usd": 1_000.0,   # smaller book
        "max_setups_per_plan": 3,
        "max_leverage": 20.0,           # conservative isolated leverage cap
        "risk_per_trade_pct": 0.025,    # still risk ~1% of (the smaller) equity per stop-out
        "max_margin_pct_per_trade": 0.20,
        "max_total_margin_pct": 0.60,
        "max_portfolio_risk_pct": 0.03,
        "min_rr": 1.0,                 # accept ~1:1 — scalps take quick, nearby targets
        "min_stop_atr_mult": 0.5,      # allow tight stops (relax the noise-stop guard)
        "max_hold_bars": 0,            # short holds (~2h on 15m); scalps don't marinate
        "plan_refresh_bars": 8,        # re-think the plan more often (~2h on 15m)
        "trail_atr_mult": 1.0,         # trail closer to lock small gains fast
        "scale_out_frac": 0.5,         # bank half at the first target, ride the rest
        "observe_every_bars": 1,       # check the open trade every finer-tf bar
    },
}


def apply_profile(name: str, target: Settings = settings) -> dict[str, Any]:
    """Apply a named profile's overrides onto ``target`` (defaults to the live singleton).

    Returns the dict of overrides that were applied (empty for ``baseline``). Raises
    ``KeyError`` for an unknown profile and ``AttributeError`` for an unknown field.
    """
    if name not in PROFILES:
        raise KeyError(f"unknown profile '{name}'. Available: {', '.join(sorted(PROFILES))}")
    overrides = PROFILES[name]
    for field, value in overrides.items():
        if not hasattr(target, field):
            raise AttributeError(f"profile '{name}' sets unknown Settings field '{field}'")
        setattr(target, field, value)
    target.strategy_profile = name
    return {**overrides, "strategy_profile": name}
