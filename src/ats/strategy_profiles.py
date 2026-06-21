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
            # Stops were 1.0 ATR (swept by noise → 69% stop-outs) then widened to 1.5, tightened
            # to 1.3 (iter8). Iter 9 tried 1.2 on the full 2026 window → −$470 (too tight: clips
            # winners into losers, win 70%→64%). 1.3 is the optimum; the residual bleed is an
            # entry-quality/direction problem, not stop distance.
            "min_stop_atr_mult": 1.3,
            # Pull the take-profit in from 3.0→2.0 ATR so the scale-out-at-TP1 leg actually
            # banks a win (the 3-ATR target was tagged on ~3% of trades). With the 1.5-ATR
            # stop this is RR≈1.33, still clearing the scalper min_rr (1.0).
            "reward_atr_mult": 2.0,
        },
        "plan": {
            "max_setups_per_plan": 3,
            # Iter 7: replan every ~3h (was 6/~1.5h). The fast 6-bar churn re-created plans
            # constantly and fed marginal entries; slow it back down so only fresh, qualified
            # setups fire.
            "plan_refresh_bars": 12,
            # Iter 7: revert the confidence loosening (0.50→0.60). The loosen flooded the book
            # with immediate-adverse entries — 33 stop-outs at -10.9% margin each (-360% total)
            # and killed the previously-profitable short book. The stop-loss bleed is an
            # entry-quality problem, so raise the conviction floor.
            "signal_min_confidence": 0.60,
            # Iter 3: entry zones now sit on the pullback side (sell rallies / buy dips), so the
            # momentum confirmation (require decision bar to close WITH the trade) directly
            # fights the design — a bounce-fill closes UP, which the gate would reject. Turn it
            # off; the pullback location is the quality filter now.
            "entry_confirmation_enabled": False,
            # Drop trades that fight the 4h trend — the biggest BTC-replay loss bucket was
            # 15m longs taken inside a 4h downtrend (bull-low/chop drift).
            "htf_trend_filter": True,
            # Iter 2: the HTF-exhaustion relief re-opened mean-reversion longs on every
            # oversold bounce inside the 4h downtrend — knife-catches that were the single
            # worst loss bucket (-92.8% margin). Turn it off so the gate stays trend-aligned.
            "counter_trend_on_htf_exhaustion": False,
            # Iter 12: regime-PnL breakdown on the full-2026 window showed the strategy makes
            # all its money in bear-low (179t/79%/+133%) and loses only in side-low (90t/42%/
            # -65%) — driven entirely by side-low LONGs (43t/40%/-70%). The pullback entries
            # whipsaw in low-vol chop. Drop sideways longs; keep the ~neutral sideways shorts.
            "sideways_block_longs": True,
        },
        "exits": {
            "max_hold_bars": 8,            # ~2h on 15m; scalps don't marinate (>0 = time-stop ON)
            # Iter 10: widen the runner trail 1.0 → 1.5. The +0.2 ATR profit-lock floor (iter7)
            # already guarantees an armed trade exits a winner, so a wider trail can't turn a
            # winner into a loss — it only lets winners ride the (large) trends in this window
            # further before the chandelier cuts them. Targets the +2.9%-avg trail bucket: more
            # runners reach the bigger trail/tp exits instead of scratching out at +0.2 ATR.
            "trail_atr_mult": 1.5,
            "scale_out_frac": 0.5,         # bank half at the first target, ride the rest
            # Iter 1: the default early protection arms a 2-ATR trail at +1 ATR favorable, so a
            # trade that reaches +1 ATR can still reverse 2 ATR into a -1 ATR LOSS — it converts
            # would-be small winners into losers (the win-rate sink). For a scalper, lock
            # no-loss instead: jump the stop to breakeven once +1 ATR (and >2x cost) is earned.
            "early_stop_mode": "breakeven",
            "sideways_early_stop_mode": "breakeven",
            # Iter 6: arm the no-loss lock sooner. At +1.0 ATR many trades reverse before they
            # ever protect; +0.6 ATR catches more of them at breakeven (a scratch beats a -1.5
            # ATR stop) — the direct win-rate lever. Drop the profit floor to 1x cost so the
            # earlier arm actually fires in low-ATR bars instead of being gated out.
            "breakeven_arm_atr": 0.6,
            "breakeven_arm_cost_mult": 1.0,
            # Iter 7: profit-lock the early arm. Parking the stop at cost-breakeven scratched
            # 94/178 trades to ~0% — non-wins that crushed win rate to 22%. Lock +0.2 ATR of
            # real profit beyond cost-breakeven so an armed-then-retraced trade exits as a
            # small WIN, not flat. The chandelier trail still ratchets above this floor.
            "breakeven_lock_atr": 0.2,
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
            "max_portfolio_risk_pct": 0.01,
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
