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
            "max_portfolio_risk_pct": 0.075,
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
            # NOTE (live↔sim gap fix): this maker model is JUSTIFIED, not optimistic — live
            # execution now places a genuine post-only resting LIMIT entry (execution.live
            # place_entry_limit → binance_futures.limit_order, timeInForce=GTX) and native
            # STOP/TP exits, so the fills these bps assume are the fills live actually gets. Do
            # NOT "correct" this back up to taker: that was only warranted under the old
            # market-order mirror, which this change abandons.
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
            # LOOP5 / Iter 5: 1.0 → 0.8 (now that the sl_tp 1.0-ATR clamp is lifted). The dominant
            # bleed is the full-stop loser bucket (~1.0R). Under the iter-3 5m close-confirm, wicks
            # that pierce the stop but reclaim the level on the 5m close are ridden through, so a
            # tighter 0.8-ATR stop shrinks every loser to ~0.8R without re-introducing the noise
            # stop-outs that sank tight stops on the old 15m-touch machine. Also raises realized
            # payoff (winner harvest 0.50 ATR / stop 0.8 ATR = 0.625R vs 0.50R at a 1.0 stop).
            # Iter 5 RESULT: 0.8 lifted PnL hugely (net +$1655→+$2505, 4/6 pass) at a small win-rate
            # cost — but BTC26 over-paid win rate (70→65%). Iter 6 RESULT (REVERTED): 0.9 recovered
            # BTC26 win (68%) but broke ETH26 (67→65%, +296→−6) — ETH and BTC want OPPOSITE stops.
            # 0.8 passes 4/6 vs 0.9's 3/6, so 0.8 stays the base.
            # LOOP5 / Iter 10: VOLATILITY-ADAPTIVE stop. BTC and ETH wanted opposite fixed stops
            # (BTC chop needs wide to survive whipsaw, ETH high-vol needs tight for payoff) — the
            # bleed is uniformly the low-pr_atr (compressed/chop) bucket (59% win vs 74-83%). So
            # tie the stop to the trailing ATR percentile: 0.7 ATR at high vol (tight, payoff),
            # widening to 1.1 ATR at low vol (survive chop). min_stop_atr_mult is the TIGHT end
            # and doubles as the risk-layer noise floor; stop_atr_wide is the chop end.
            # Iter 10 RESULT: tight end 0.7 ALSO dropped the noise-admission floor → volume flooded
            # (244→361) with marginal setups, win rate fell, PnL cratered. The tight end must stay
            # 0.8 (= the proven iter8 admission floor); only the chop-end widening is new.
            # Iter 11 RESULT (REVERTED): widening chop stops lifted borderline chop setups ABOVE
            # the noise-rejection floor (which had been silently filtering them) → +volume of the
            # 59%-win chop bleed, PnL cratered. The fixed-0.8 stop's noise rejections are a hidden
            # quality filter; keep them. Adaptive stop disabled.
            # LOOP5 / Iter 13 (REVERTED): the absolute/relative-vol adaptive stop helped BTC26 but
            # traded it against ETH26 / BTC25H2 — no net gain over the fixed 0.8. BEST SETUP keeps
            # the fixed 0.8 stop with the adaptive machinery present but DISABLED.
            "min_stop_atr_mult": 0.8,
            "adaptive_stop_enabled": False,
            "stop_atr_wide": 1.15,
            # LOOP5 / Iter 12: volatility-scaled sizing — concentrate risk on high-pr_atr (trend,
            # higher win + bigger winners) and starve low-pr_atr chop. The lever to push BTC26
            # past its ~+$91 expectancy ceiling without changing which setups trade.
            # Iter 12 RESULT (REVERTED): helped 2025 windows but hurt both 2026 windows (their edge
            # isn't in the highest-vol bars) → 3/6. Disabled; revisit params later.
            "vol_sizing_enabled": False,
            "vol_size_min": 0.6,
            "vol_size_max": 1.6,
            # Pull the take-profit in from 3.0→2.0 ATR so the scale-out-at-TP1 leg actually
            # banks a win (the 3-ATR target was tagged on ~3% of trades). With the 1.5-ATR
            # stop this is RR≈1.33, still clearing the scalper min_rr (1.0).
            # SPEC3-LOOP / Iter 2 attempt 1 (REVERTED): 2.0 → 1.2 to fire the scale-out on more
            # winners. Degraded TRAIN on ALL three coins (BTC −$45, ETH −$66, SOL −$129) with no
            # win-rate gain — pulling TP1 in caps the genuine runners' first half (where the PnL
            # concentrates) more than the extra scale-out banking adds. Reverted to 2.0.
            "reward_atr_mult": 2.0,
            "entry_pullback_atr_frac": 0.25,
        },
        "plan": {
            "max_setups_per_plan": 3,
            # Iter 7: replan every ~3h (was 6/~1.5h). The fast 6-bar churn re-created plans
            # constantly and fed marginal entries; slow it back down so only fresh, qualified
            # setups fire.
            "plan_refresh_bars": 6,
            # Iter 7: revert the confidence loosening (0.50→0.60). The loosen flooded the book
            # with immediate-adverse entries — 33 stop-outs at -10.9% margin each (-360% total)
            # and killed the previously-profitable short book. The stop-loss bleed is an
            # entry-quality problem, so raise the conviction floor.
            # Iter 20: with the now-robust exit machine (+0.45 arm, +0.42 lock, 1.0-ATR stop,
            # maker fees) the book is solidly +EV, so a modest loosen 0.60 → 0.57 admits the
            # next-best setups as ~+EV volume — crossing the 300-trade floor and adding PnL. This
            # is NOT the iter6.5 disaster (0.50 + refresh-6 under look-ahead = 22% win): the
            # quality bar moves only slightly and win-rate headroom (71% vs 60%) absorbs it.
            # SPEC3-LOOP / Iter 2 attempt 2 (REVERTED — overfit): 0.70 → 0.75 raised TRAIN (ETH
            # 71→74% fail→pass, BTC +$23, all win ≥73%) but a controlled A/B on the same validation
            # window (2025-09→2026-02) showed it DEGRADED OOS on all three coins (BTC +$35→−$125,
            # ETH $831→$506, SOL $595→$530). TRAIN↑ / VALIDATION↓ is the overfitting signature — the
            # stricter floor memorizes 2025 entry quality that doesn't transfer to the 2026 regime.
            # Non-degradation veto → rejected. Base floor stays 0.70.
            # LOOP7 / Iter 4 (hybrid-engine branch, objective = max portfolio PnL): the 0.70 floor
            # was badly MISCALIBRATED — it suppressed good ETH/SOL setups. A TRAIN sweep is
            # monotone in PnL as the floor drops (0.70→$2154, 0.60→$2623, 0.55→$3352, 0.50→$3387,
            # 0.40→$3762) and — critically — the gain GENERALIZES: VALIDATION (incl. the 2026
            # drawdown) rises too (0.70→$1500, 0.60→$1669, 0.55→$1903, 0.50→$1907). ETH win-rate
            # RISES 69→76% as the floor drops (the gate was filtering winners, not losers). 0.55 is
            # the robust knee: below it TRAIN keeps climbing on noise but VALIDATION flattens and
            # SOL bleeds OOS ($639→$473 at 0.50), so 0.55 banks the validated edge without the
            # overfit tail. +$1198 TRAIN / +$403 VALIDATION portfolio vs the 0.70 base.
            "signal_min_confidence": 0.55,
            # LOOP6 / Iter 6: volatility-conditional confidence floor on ABSOLUTE atr_pct. Iter 5
            # proved the chop floor fixes a bleeding window (BTC25h2 148→255, win 70→73) but
            # gated on the within-symbol pr_atr percentile, which also gated ETH's quiet bars and
            # broke ETH26h1. atr_pct (ATR/price) is structurally lower on BTC (p35≈0.0023) than
            # ETH (p20≈0.0031), so a 0.0023 cutoff tightens BTC's choppiest ~35% of bars to a 0.80
            # bar while touching ~zero ETH bars. Targets the chop entry-quality bleed cross-symbol
            # without subtracting a regime (the iter1 non-local trap). Trend bars keep the 0.70 base.
            # LOOP6 / Iter 7 (REVERTED): widened cutoff 0.0023 → 0.0028. Made BTC26h1 MUCH worse
            # (−19.60 → −125.43, win 68→64) — removing the moderate-vol bear-low/side-low trades
            # reshuffled the single-position sequence into worse fills (non-locality again; even
            # the chop FLOOR triggers it when it removes too many trades). 0.0023 is the sweet spot.
            # LOOP7 / Iter 1 (hybrid-engine branch): TRAIN diagnosis shows the band atr_pct
            # 0.0023–0.0030 is net-NEGATIVE on TRAIN for BOTH BTC (75t/69%/−$13) and ETH
            # (15t/47%/−$62), while SOL has ZERO trades that low (SOL is structurally high-vol).
            # The 0.0023 cutoff lets this bleed band escape the 0.80 floor. Widen to 0.0030 to
            # gate the band's marginal (<0.80 conf) entries cross-symbol while sparing SOL
            # entirely. Motivated on TRAIN-2025 only (LOOP6's 0.0028 caution was a 2026-holdout
            # non-locality observation, off-limits as a motivation here).
            "chop_atr_pct_max": 0.0030,
            "chop_min_confidence": 0.80,
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
            # LOOP6 / Iter 1 (REVERTED): re-tested blocking sideways SHORTS under the +EV LOOP5
            # config. Regressed broadly (btc25h1 −92, eth25h2 −149, etc.; only eth26h1 +106) and
            # cut trade counts toward the 150 floor. The single-position non-locality holds even
            # under a profitable book — subtractive regime blocks reshuffle the whole sequence.
            # Confirms: only UNIVERSAL per-trade levers are robust here. Flag stays OFF.
            "sideways_block_shorts": False,
            # LOOP6 holdout: a counter-trend mean-reversion sub-strategy for the low-vol chop the
            # trend engine can't trade (BTC 2026). It FADES the recent range (buy the bottom / sell
            # the top → mid) where sideways_block_longs/htf_trend_filter suppress the trend entries.
            # Gated on absolute atr_pct at the same band the chop floor uses, so it fires on BTC's
            # choppiest bars and ~never on ETH/SOL. Tune mr_edge_frac/mr_min_range_atr on BTC 26h1.
            "mr_enabled": True,
            "mr_atr_pct_max": 0.0030,
            "mr_range_lookback": 20,
            "mr_edge_frac": 0.15,
            "mr_rsi_os": 35.0,
            "mr_rsi_ob": 65.0,
            "mr_stop_buffer_atr": 0.5,
            "mr_min_range_atr": 2.0,
        },
        "exits": {
            "max_hold_bars": 8,            # ~2h on 15m; scalps don't marinate (>0 = time-stop ON)
            # SPEC3-LOOP / Iter 3 (REVERTED — non-robust): conditional loser-cut. Mechanically it
            # did exactly what it targets — on TRAIN it shrank avg_loss (BTC −7.1→−6.7%, ETH
            # −10.8→−9.9%, SOL −11.7→−10.7%) and lifted payoff on all three (0.48→0.51, 0.54→0.59,
            # 0.53→0.58) while leaving winners untouched. But it costs ~1-2pts win-rate (clips
            # would-have-recovered trades) and is the familiar BTC↔ETH split: on the validation
            # window (2025-09→2026-02, incl. the 2026 drawdown) it HELPED BTC (+$35→+$78, its design
            # goal) and SOL (+$595→+$793) yet HURT ETH (+$831→+$451, −$380) — ETH even flipped
            # TRAIN↑/VAL↓. Aggregate validation PnL fell (~−$139) and win-rate dropped on all three →
            # non-degradation veto rejects it. Infra kept (default 0.0 in ExitConfig) and DISABLED,
            # alongside adaptive_stop / vol_sizing; revisit only as a regime/symbol-gated lever.
            "loss_cut_hold_frac": 0.0,
            "loss_cut_atr_mult": 0.0,
            # Iter 13: TIGHTEN the runner trail 1.5 → 0.9 (honest-fills re-baseline). Under the
            # corrected resting-limit fills the avg winner collapsed to +0.27 ATR: a 1.5-ATR
            # chandelier never ratchets above the +0.2 lock floor until +1.7 ATR, so every
            # medium runner (peak +0.8..1.5 ATR) retraces all the way back to +0.2 and exits
            # tiny while losers run the full 1.3-ATR stop (−11.3%) — an inverted 1:4.8 payoff.
            # A 0.9-ATR chandelier ratchets above the floor from +1.1 ATR on, so medium runners
            # bank +0.4..0.8 ATR instead of +0.2. Win-rate headroom (67% vs 60% target) absorbs
            # any early-chop cost; the +0.2 lock still guarantees no armed trade becomes a loss.
            # LOOP6 / Iter 4 (REVERTED): loosened trail 0.6 → 1.0. Helped BTC's larger windows
            # (25h1 +9, 25h2 +16) but hurt BTC26h1 (−8) and ALL ETH (−52/−32/−24), dropping
            # ETH26h1 below $200. The chop give-back exceeds the trend-ride gain. 0.6 is near
            # the trail optimum; like every universal knob it trades BTC-chop ↔ ETH-trend.
            "trail_atr_mult": 0.6,
            # LOOP6 / Iter 8 (REVERTED): within-symbol vol-conditional trail (loosen to 1.2 ATR on
            # top-third pr_atr bars). Helped some windows (BTC25h1 +13, ETH26h1 +41) but made the
            # BTC26h1 holdout WORSE (−19.6 → −37.4) and dented ETH25h1/25h2. In BTC26h1 even the
            # high-pr_atr bars are low-amplitude chop, so a looser trail just gives back more. The
            # infra (trail_atr_mult_trend / trail_trend_pr_atr_min in ExitConfig + manager.py) is
            # kept but DISABLED. Confirms BTC26h1's winner-size ceiling can't be lifted by trail.
            "trail_atr_mult_trend": 0.0,
            "trail_trend_pr_atr_min": 0.0,
            "scale_out_frac": 0.5,         # bank half at the first target, ride the rest
            # Iter 1: the default early protection arms a 2-ATR trail at +1 ATR favorable, so a
            # trade that reaches +1 ATR can still reverse 2 ATR into a -1 ATR LOSS — it converts
            # would-be small winners into losers (the win-rate sink). For a scalper, lock
            # no-loss instead: jump the stop to breakeven once +1 ATR (and >2x cost) is earned.
            "early_stop_mode": "breakeven",
            "sideways_early_stop_mode": "breakeven",
            "sideways_exit_mode": "trend",
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
            # LOOP6 / Iter 2 (REVERTED): raised harvest floor arm 0.50→0.58 / lock 0.45→0.53.
            # Helped ETH but badly hurt ALL BTC (win −4–5pts). Arming LATER converts BTC's
            # marginal ~0.5-ATR peakers into stops. BTC and ETH want OPPOSITE arm timing.
            # LOOP6 / Iter 3 (REVERTED): arm EARLIER (0.42) / lock 0.38. Raised win rate
            # (68→70 on BTC26h1) but LOWERED PnL (banks tiny +0.38-ATR wins) and crashed
            # ETH25h1 (−374). The arm/lock floor is already at its PnL-optimal point (0.50/0.45);
            # moving it either way trades PnL↔win-rate. Knobs exhausted — need a STRUCTURAL edge.
            "breakeven_arm_atr": 0.50,
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
            "breakeven_lock_atr": 0.45,
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
    # Test-harness override (inert unless ATS_PROFILE_OVERRIDE is set): a comma-separated list of
    # "group.field=value" applied on top of the named profile, so a knob sweep doesn't need a file
    # edit per run. The winning config is always baked back into the profile dict above; this is a
    # convenience for the LOOP, not a shipping path. Values are coerced to the existing field type.
    import os
    raw = os.environ.get("ATS_PROFILE_OVERRIDE", "").strip()
    if raw:
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            key, _, val = item.partition("=")
            group, _, field = key.strip().partition(".")
            section = getattr(target, group)
            cur = getattr(section, field)
            if isinstance(cur, bool):
                coerced: Any = val.strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(cur, int) and not isinstance(cur, bool):
                coerced = int(val)
            elif isinstance(cur, float):
                coerced = float(val)
            else:
                coerced = val.strip()
            setattr(section, field, coerced)
            applied[f"{group}.{field}"] = coerced
    target.strategy_profile = name
    return {**applied, "strategy_profile": name}
