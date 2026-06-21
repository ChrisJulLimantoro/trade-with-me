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
            # Iter 14: correct the round-trip cost model for this strategy's order types. The
            # entry is a RESTING LIMIT (wick_limit) → a MAKER fill at the limit price: it earns
            # the maker fee (~2 bps) and suffers ZERO adverse slippage (a limit fills at your
            # price or not at all). The default 5 bps taker + 2 bps slippage charged on BOTH
            # legs (14 bps round-trip) wrongly modelled the entry as a taker market order. The
            # honest round-trip here is maker-in (2 bps, 0 slip) + taker-out (5 bps, 2 slip) =
            # 9 bps. The symmetric model bakes both legs into each banked fraction, so per-leg
            # cost 4.5 bps reproduces the correct 9 bps total. Exit stays full taker+slippage
            # (conservative: TP/trail limit exits are also maker but not credited here). This
            # removed ~5 bps notional ≈ 1% margin of PHANTOM cost per trade (~$2/trade × 264).
            "fee_bps": 3.5,
            "slippage_bps": 1.0,
            "min_rr": 1.0,                 # accept ~1:1 — scalps take quick, nearby targets
            # Stops were 1.0 ATR (swept by noise → 69% stop-outs) then widened to 1.5, tightened
            # to 1.3 (iter8). Iter 9 tried 1.2 on the full 2026 window → −$470 (too tight: clips
            # winners into losers, win 70%→64%). 1.3 was the optimum UNDER THE OLD MACHINE.
            # Iter 17: re-test at 1.1. The earlier +0.45 ATR arm (iter16) now protects genuine
            # winners long before any 1.1-ATR adverse excursion, so tightening mostly governs the
            # immediate-adverse losers that never arm: avg loser −10.4% → ~−8.8%, RR 1.54 → 1.82.
            # This also doubles as the noise-stop rejection floor, so 1.1 admits more setups →
            # more trades (toward the 300 floor). Win-rate headroom (74% vs 60%) absorbs any clip.
            # Iter 18: push to 1.0 (the floor — sl_tp clamps stop_atr to max(min,1.0)). iter17 at
            # 1.1 held win rate (74→73%) and flipped PnL positive, confirming the early arm shields
            # winners; 1.0 shrinks losers further (−9.5% → ~−8.6%) and admits still more setups.
            "min_stop_atr_mult": 1.0,
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
            # Iter 20: with the now-robust exit machine (+0.45 arm, +0.42 lock, 1.0-ATR stop,
            # maker fees) the book is solidly +EV, so a modest loosen 0.60 → 0.57 admits the
            # next-best setups as ~+EV volume — crossing the 300-trade floor and adding PnL. This
            # is NOT the iter6.5 disaster (0.50 + refresh-6 under look-ahead = 22% win): the
            # quality bar moves only slightly and win-rate headroom (71% vs 60%) absorbs it.
            "signal_min_confidence": 0.57,
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
            # Iter 15 (REVERTED): blocking sideways SHORTS regressed PnL (−$194 → −$247). Under
            # the single-position model, removing a regime is NON-LOCAL — freeing the slot in
            # side-* let the engine fill different, worse trades and flipped bear-low SHORT from
            # +13% to −40% (same ~135 trades, reshuffled timing). Subtractive regime blocks are
            # unreliable here; the robust levers are universal per-trade ones. Flag kept OFF.
            "sideways_block_shorts": False,
        },
        "exits": {
            "max_hold_bars": 8,            # ~2h on 15m; scalps don't marinate (>0 = time-stop ON)
            # Iter 13: TIGHTEN the runner trail 1.5 → 0.9 (honest-fills re-baseline). Under the
            # corrected resting-limit fills the avg winner collapsed to +0.27 ATR: a 1.5-ATR
            # chandelier never ratchets above the +0.2 lock floor until +1.7 ATR, so every
            # medium runner (peak +0.8..1.5 ATR) retraces all the way back to +0.2 and exits
            # tiny while losers run the full 1.3-ATR stop (−11.3%) — an inverted 1:4.8 payoff.
            # A 0.9-ATR chandelier ratchets above the floor from +1.1 ATR on, so medium runners
            # bank +0.4..0.8 ATR instead of +0.2. Win-rate headroom (67% vs 60% target) absorbs
            # any early-chop cost; the +0.2 lock still guarantees no armed trade becomes a loss.
            "trail_atr_mult": 0.9,
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
            # Iter 16: arm the no-loss lock even sooner, 0.6 → 0.45 ATR. The −659% sl bucket is
            # trades that reverse to the full 1.3-ATR stop without ever reaching the +0.6 arm. A
            # trade that peaks +0.45..0.6 ATR then reverses is currently a full −10% LOSS; arming
            # at +0.45 locks it as a +0.35 ATR WIN instead — a ~+13% per-trade swing on exactly
            # the marginal trades. A universal win-rate/expectancy lever (not a subtractive block).
            # The 0.9-ATR chandelier still rides genuine runners above the lock, so big winners
            # are preserved; only stalled trades exit at the +0.35 floor.
            "breakeven_arm_atr": 0.45,
            "breakeven_arm_cost_mult": 1.0,
            # Iter 7: profit-lock the early arm. Parking the stop at cost-breakeven scratched
            # 94/178 trades to ~0% — non-wins that crushed win rate to 22%. Lock real profit
            # beyond cost-breakeven so an armed-then-retraced trade exits as a small WIN, not
            # flat. The chandelier trail still ratchets above this floor.
            # Iter 13: raise the floor 0.2 → 0.35 ATR. The low-peak runners (peak +0.6..1.1 ATR)
            # are below where even the tightened 0.9-ATR trail binds, so they retrace to this
            # floor — banking +0.35 instead of +0.2 ATR lifts the whole 156-trade trail bucket.
            # Iter 19: raise the floor 0.35 → 0.42 ATR (just under the 0.45 arm). The bulk of
            # winners (~190 trail exits) harvest exactly at this floor; lifting it +0.07 ATR adds
            # ~+0.6% margin to each. The 0.03-ATR give-back still lets genuine runners arm the
            # 0.9-ATR trail and ride higher; floor-harvested trades just bank more.
            "breakeven_lock_atr": 0.42,
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
