# BTC Scalper Win-Rate Optimization Loop

> ⚠️ **All PnL/win-rate numbers in Loops 1–2 below are INFLATED by an entry look-ahead bug
> (found 2026-06-21).** The `wick_limit` entry filled a trade on the *same closed bar* whose
> indicators authorized it, at an intrabar wick price the bar had already left — a fill no live
> system can reach. Switching to honest `close` fills flipped the result from profit to loss,
> confirming the edge was the artifact. Fixed via an arm→pending→fill resting-order lifecycle.
> **See "Entry look-ahead correction" at the bottom — every iteration's values must be
> re-baselined before trusting them.**

**Goal (loop end condition):** `closed_trades >= 60` AND `win_rate >= 50%` AND `pnl_usd > 0`
on `BTCUSDT 15m`, profile `scalper`, window `2026-05-01 → 2026-06-19`.

**Verify command:**
```
uv run ats engine replay --symbol BTCUSDT --timeframe 15m --from 2026-05-01 --to 2026-06-19 --profile scalper --run-label <label>
```

---

## Baseline — `replay_BTCUSDT_15m_20260620-164107.log` (label: htf-agent)

| metric | value |
|---|---|
| closed | 115 |
| win rate | 37% |
| pnl_usd | **-$309.75** |
| plan_confirm_rate | 17% |

### Diagnosis (exit-reason × direction)

| reason | n | sum margin_pnl |
|---|---|---|
| sl | 52 | **-384%** |
| expiry | 20 | -102% |
| trail | 24 | +197% |
| tp | 10 | +105% |
| breakeven | 9 | +29% |

- **LONG** 48 trades, 38% win, **-74.7%** — counter-trend longs in a -18.5% downtrend (BTC 77,083 → 62,757). EMA50-cross trend filter whipsaws (went LONG @ 77,083 on day 1, the top).
- **SHORT** 67 trades, 37% win, **-80.2%**. SHORT sl alone = -248%. With-trend shorts still lose because entries chase: `wick_limit` fills shorts at the zone *low* and `entry_confirmed` demands down-momentum → sells the local low right before a bounce → stopped 1.5 ATR up.

### Root-cause shortlist (levers)
1. **Loose early protection** — `early_stop_mode="trail"` arms a **2-ATR** trail at +1 ATR favorable, so a +1 ATR winner can reverse 2 ATR into a -1 ATR *loss*. Converts small winners → losers; suppresses win rate.
2. **Whipsaw entry timing** — momentum confirmation + breakdown fills = buy-high/sell-low.
3. **Counter-trend longs** — EMA50-cross trend filter is laggy/whipsaw-prone.

---

## Iter 1 — early breakeven lock (label: iter1-be-lock)

**Change:** scalper `exits.early_stop_mode` `"trail"` → `"breakeven"` (+ sideways). Lock no-loss
once +1 ATR favorable, instead of arming a loose 2-ATR trail that gives a winner back into a loss.

| metric | baseline | iter1 |
|---|---|---|
| closed | 115 | 113 |
| win rate | 37% | **41%** |
| pnl_usd | -$309.75 | -$321.37 |
| sl count | 52 | **27** |
| expiry count | 20 | 29 |

**Read:** stop-outs halved (small losers → breakeven), win rate +4pts — **kept**. But PnL flat:
the expiry bucket grew (−142%) and the surviving stops are big knife-catches (avg −10.9%).
Direction split: **LONG 45t / 38% / −92.8%** (counter-trend, the anchor); SHORT 68t / 43% / −67.9%.
Even pure shorts still lose → need trend alignment *and* short entry quality.

---

## Iter 2 — stop counter-trend mean-reversion entries (label: iter2-no-countertrend)

**Change:** scalper `plan.counter_trend_on_htf_exhaustion` → `False`.

**Result: NO-OP — byte-identical to iter1** (113t, 41%, −$321.37, LONG 45/−92.8%, SHORT 68/−67.9%).
The counter-trend longs are **not** from the exhaustion relief — they come through
`_htf_trend` returning `"up"` whenever price pops above the lagging 4h EMA50 on a bounce. The
price-vs-EMA50 trend test whipsaws. Kept the flag off (harmless, correct intent), but it's not
the lever. **Real binding problem: entry timing — both directions lose at ~40% because the
detector chases** (wick_limit fills shorts at the zone low + momentum confirmation = sell-low/
buy-high).

---

## Iter 3 — pullback entry zones + drop momentum confirmation (label: iter3-pullback-entry)

**Change:**
- `sl_tp.default_band` now direction-aware: a **short** zone sits 0.25 ATR ABOVE close
  (limit-sell into a bounce, fill at zone low); a **long** zone sits 0.25 ATR BELOW close
  (limit-buy a dip, fill at zone high). Stops chasing the extreme of an impulse leg.
- scalper `plan.entry_confirmation_enabled` → `False` (momentum confirmation requires the bar to
  close WITH the trade, which rejects exactly the bounce-fills the new zones are designed to take).

| metric | iter2 | iter3 |
|---|---|---|
| closed | 113 | 225 |
| win rate | 41% | 39% |
| pnl_usd | -$321 | **-$655** |

**Read:** worse *overall*, but it split the problem cleanly:
- **SHORT 135t / 43% / −32.4%** — pullback entry nearly halved per-trade short loss (iter2 short avg −1.0%/t → −0.24%/t). The pullback location works *with* the trend.
- **LONG 90t / 33% / −295.1%** — dropping confirmation flooded counter-trend longs; catastrophic.

Conclusion: keep pullback entries; the loss is entirely the counter-trend longs leaking through
the whipsawing `price-vs-EMA50` trend test.

---

## Iter 4 — slow-EMA-stack trend filter (label: iter4-ema-stack-trend)

**Change:** `_htf_trend` now reads the **ema_50 vs ema_200** stack (down when ema_50 < ema_200)
instead of `close vs ema_50`. The slow stack stays bearish through intraday bounces, so the
`htf_trend_filter` blocks longs for the whole established downtrend instead of re-admitting them
every time price pops above the fast EMA. **(iter4 is the best config so far.)**

| dir | iter3 | iter4 |
|---|---|---|
| SHORT | 135t / 43% / −32.4% | **97t / 40% / +36.1%** (profitable!) |
| LONG | 90t / 33% / −295% | 62t / 34% / −197.3% |
| **total** | 225t / 39% / −$655 | 159t / 38% / −$322 |

**Read:** shorts flipped **positive** (+$70-ish of margin). But 62 longs still leak: the ema_50/
ema_200 *cross* lags ~2 weeks at the May top, admitting longs the whole early drop.

---

## Iter 5 — block longs on loss of the 4h 200-EMA (label: iter5-block-longs-200ema)

**Change:** `_htf_trend` is "up" only when ema_50 ≥ ema_200 **AND** close ≥ ema_200. Losing the
200 EMA flips to "down" days before the slow cross, so the early-drop longs are blocked too.

**Result: REGRESSION — reverted.** closed=179, win 39%, **−$420** (worse than iter4 −$322).
LONG 61t/−194% (barely changed — the early longs are taken when BTC is genuinely above its 4h
200-EMA at all-time highs; no lagging EMA catches a top in real time). And SHORT **+36.1% →
−15.8%**: the `close<ema_200` term flips trend to "down" too early in the choppy top, unblocking
shorts into the whipsaw. Lesson: blocking longs and timing shorts are coupled through one gate;
iter4's slower filter took *fewer, cleaner* late shorts. Reverted to iter4's `ema_50 vs ema_200`.

---

## Iter 6 — earlier breakeven lock + revert iter5 (label: iter6-early-breakeven)

**Changes:**
- Revert `_htf_trend` to iter4 (`ema_50 ≥ ema_200`).
- scalper `exits.breakeven_arm_atr` 1.0 → **0.6**, `breakeven_arm_cost_mult` 2.0 → **1.0**. Lock
  no-loss after +0.6 ATR (was +1.0) so more trades reach protection before reversing — the direct
  win-rate lever, applied to both directions.

---

## Iter 6.5 — `loosen-signal-rules` (REGRESSION, the loop's starting log)

Someone loosened entries (`signal_min_confidence` 0.55→**0.50**, `plan_refresh_bars`→**6**) to lift
volume. It backfired hard: **178t / 22% / −$424.77**.

### Diagnosis (the loop baseline)

| reason | n | sum margin | avg |
|---|---|---|---|
| sl | 33 | **−359.9%** | −10.90% |
| expiry | 20 | −106.6% | −5.33% |
| breakeven | **94** | +42.9% | +0.46% |
| trail | 18 | +92.7% | +5.15% |
| tp | 13 | +118.5% | +9.12% |

Two killers: (1) **94/178 trades (53%) scratched to ~0% at the cost-breakeven stop** — non-wins
that crushed win rate; (2) **33 stop-outs at −10.9% each (−360%)** — immediate-adverse entries the
loosened confidence let in. LONG 70t/23%/−155%, SHORT 108t/21%/−58% (the loosen even broke the
previously-profitable short book).

---

## Iter 7 — revert loosen + **profit-lock** (label: iter7-profitlock) ✅ **near target**

**Changes:**
1. Revert entry loosen: `signal_min_confidence` 0.50 → **0.60**, `plan_refresh_bars` 6 → **12**.
   Cut the immediate-adverse entries that fed the stop-loss bleed.
2. **New `exits.breakeven_lock_atr` = 0.2** (+ code: `profit_lock_stop()` in reconcile.py,
   threaded through `step_trade` / exits manager). When the early arm fires at +0.6 ATR, the stop
   is now locked **+0.2 ATR of real profit beyond the cost-breakeven** instead of at flat. An
   armed-then-retraced trade now exits as a small **win** (labelled `trail`, pnl>0), not a 0.00%
   scratch. The chandelier trail still ratchets above this floor.

| metric | baseline (loosen) | iter7 |
|---|---|---|
| closed | 178 | **104** |
| win rate | 22% | **72%** |
| pnl_usd | −$424.77 | **−$6.47** |

| reason | n | sum margin | avg |
|---|---|---|---|
| sl | 17 | **−190.9%** | −11.23% |
| expiry | 12 | −67.6% | −5.63% |
| breakeven | 3 | +11.2% | +3.72% |
| tp | 6 | +55.0% | +9.16% |
| trail | **66** | +189.1% | +2.87% |

**Read:** profit-lock converted the scratch bucket into 66 small trail wins → win rate 22%→**72%**,
and PnL from −$425 to **−$6.47 (breakeven)**. Conditions met: closed≥60 ✓, win≥50% ✓; **only pnl>0
remains**. SHORT 71t/72%/**+22.6%** (profitable); LONG 33t/73%/−25.8%. The whole residual loss is
the 29 unprotected losers (sl+expiry = −259%) that go adverse from entry — winners (+255%) almost
cover them. Next: shrink the loser size.

---

## Iter 8 — tighten initial stop 1.5 → 1.3 ATR (label: iter8-tighter-stop) ✅✅ **ALL CONDITIONS MET**

**Change:** scalper `risk.min_stop_atr_mult` 1.5 → **1.3**. With the iter7 profit-lock arming on the
*favorable* side, the initial stop distance now only controls how far an adverse-from-entry loser
runs before it's cut. Tightening it shrinks every loser ~13% and lifts RR (2.0/1.3 ≈ 1.54). Winners
were unaffected (they arm protection before any 1.3-ATR adverse excursion).

| metric | iter7 | **iter8** | target |
|---|---|---|---|
| closed | 104 | **108** | ≥ 60 ✓ |
| win rate | 72% | **77%** | ≥ 50% ✓ |
| pnl_usd | −$6.47 | **+$166.38** | > 0 ✓ |
| total margin | −3.2% | **+83.2%** | — |

| reason | n | sum margin | avg |
|---|---|---|---|
| sl | 17 | −182.9% | −10.76% |
| expiry | 8 | −40.2% | −5.02% |
| breakeven | 10 | +44.0% | +4.40% |
| tp | 9 | +90.0% | +10.00% |
| trail | 64 | +172.2% | +2.69% |

**Read:** the tighter stop turned the residual breakeven into a clear profit — **LONG 37t/81%/+5.5%
(now positive) and SHORT 71t/75%/+77.7%**. The expiry bucket also shrank (12→8 trades) as some
would-be expiries were cut earlier by the closer stop. **Loop end condition satisfied: closed=108 ≥
60, win=77% ≥ 50%, pnl=+$166.38 > 0.**

---

## Summary — problems found and what fixed them

**Where we started (loop baseline `loosen-signal-rules`): 178t / 22% win / −$424.77.**
**Where we ended (iter8): 108t / 77% win / +$166.38.**

### Problems (root causes)
1. **The cost-breakeven scratch epidemic.** The early no-loss arm parked the stop at the cost-only
   breakeven, so 94/178 trades (53%) exited at ~0.00% — counted as *non-wins*. This single
   mechanic capped win rate at ~22% even though PnL of those trades was roughly flat.
2. **Loosened entry confidence flooded the book with garbage.** `signal_min_confidence` 0.50 +
   `plan_refresh_bars` 6 admitted immediate-adverse setups: 33 stop-outs at −10.9% each (−360%
   margin) and broke the previously-profitable short book.
3. **Oversized losers.** The 1.5-ATR initial stop meant every adverse-from-entry trade bled a full
   −10.9% of margin while the profit-locked winners were small — an inverted payoff ratio.

### Solutions (what changed)
1. **Profit-lock (new `exits.breakeven_lock_atr`, = 0.2).** New `profit_lock_stop()` in
   `reconcile.py`, threaded through `step_trade` and the exits manager. The early arm now locks
   **+0.2 ATR of real profit beyond cost-breakeven**, so an armed-then-retraced trade exits as a
   small *win*, not a scratch. → win rate 22% → 72%.
2. **Reverted the entry loosen:** `signal_min_confidence` 0.50 → **0.60**, `plan_refresh_bars`
   6 → **12**. Cut the immediate-adverse entries feeding the stop bleed (sl count 33 → 17).
3. **Tightened the initial stop:** `min_stop_atr_mult` 1.5 → **1.3**. Shrank loser size, lifted RR,
   flipped PnL from −$6.47 to **+$166.38**.

### Files touched
- `src/ats/config.py` — new `ExitConfig.breakeven_lock_atr`.
- `src/ats/execution/reconcile.py` — new `profit_lock_stop()`, `breakeven_lock_atr` param + arm logic.
- `src/ats/engine/exits/manager.py` — thread `breakeven_lock_atr` into `step_trade`.
- `src/ats/strategy_profiles.py` — scalper: confidence 0.60, refresh 12, `breakeven_lock_atr` 0.2,
  `min_stop_atr_mult` 1.3.

**Tests:** `tests/test_step_trade.py` 40/40 pass; full suite 350 pass, 3 skip, 2 *pre-existing*
failures (observer-gating + llm-client-default — unrelated, fail with these changes stashed).

---

# LOOP 2 — harder targets, longer window

**New end condition:** `trades_opened >= 300` AND `win_rate >= 60%` AND `pnl_usd >= $200`,
window **`2026-01-01 → 2026-06-19`** (5.5 months, multi-regime: uptrend → downtrend → range).

```
uv run ats engine replay --symbol BTCUSDT --timeframe 15m --from 2026-01-01 --to 2026-06-19 --profile scalper --run-label <label>
```

## Iter 9 baseline (loop-1 config on the full window) — `iter9-baseline-fullwindow`

**365t / 70% / +$40.37.** Trade-count ✓ and win-rate ✓ already met; only PnL short. Dominant bleed
unchanged: **82 stop-outs = −895% margin**. LONG 130t/66%/−107% (net loser), SHORT 235t/72%/+127%.

## Iter 9b — tighten stop 1.3 → 1.2 (`iter9-stop12`) — REVERTED

**370t / 64% / −$470.65.** Backfired: at 1.2 ATR the stop clips winners into losers (win 70→64%).
**1.3 is the stop optimum**; the bleed is not stop distance. Reverted to 1.3.

## Iter 10 — widen runner trail 1.0 → 1.5 (`iter10-wide-trail`) — KEPT

**364t / 71% / +$69.57.** The +0.2 ATR profit-lock floor means a wider trail can't create a loss; it
lets winners ride the (large) trends further. Net +$30. More trades reached the big TP bucket
(26→29). Kept.

## Iter 11 — ride more past TP1, scale-out 0.5 → 0.3 (`iter11-ride-more`) — REVERTED

**364t / 71% / +$67.87.** Flat — post-TP1 runners give back as much as they gain on average.
Reverted to 0.5. Winner-side levers had plateaued ~$70.

## Iter 12 — **block LONGs in sideways regimes** (`iter12-block-sideways-longs`) ✅✅ **ALL MET**

The breakthrough came from a **PnL-by-regime** breakdown, not more knob-twisting:

| regime cell | n | win | margin | read |
|---|---|---|---|---|
| bear-low | 179 | 79% | **+133%** | the profit center (with-trend shorts) |
| bull-low | 87 | 82% | −18% | small loss, fine |
| **side-low** | **90** | **42%** | **−65%** | the sink — driven by **side-low LONGs (43t/40%/−70%)** |

A trend-pullback strategy has **no long edge in low-vol chop**: pullback entries are pure noise there
and whipsaw. Added config `plan.sideways_block_longs` (+ a gate in `create_plan` that drops `long`
from `allowed_directions` in any `side-*` regime) and turned it on for scalper. Removes the −70%
bucket while keeping the ~neutral sideways shorts and the trade-count floor.

| metric | iter9 baseline | **iter12** | target |
|---|---|---|---|
| trades opened | 366 | **312** | ≥ 300 ✓ |
| win rate | 70% | **76%** | ≥ 60% ✓ |
| pnl_usd | +$40.37 | **+$283.94** | ≥ $200 ✓ |
| total margin | +20% | **+142%** | — |

side-low flipped −65% → **+14%**; LONG book −107% → −8% (only the high-win-rate bull-low longs
remain); SHORT +150%. **Loop 2 end condition satisfied.**

---

## Summary — Loop 2 (problems & solutions)

**Start (loop-1 config, full window): 365t / 70% / +$40.** **End (iter12): 311t / 76% / +$284.**

### Problems
1. **Stop distance was already optimal at 1.3 ATR** — tightening to 1.2 destroyed PnL (−$470) by
   converting winners to losers. The −895% stop bucket is *entry* quality, not stop width.
2. **Winner-side tuning plateaued ~$70** — wider trail helped marginally (+$30); riding more past
   TP1 did nothing. Symmetric winner/loser sizing left the system barely positive.
3. **The real sink was one regime.** PnL-by-regime: the strategy earns everything in **bear-low
   (+133%)** and bleeds almost entirely in **side-low (−65%)**, specifically **side-low LONGs
   (40% win, −70%)** — the trend-pullback entries whipsaw in low-vol ranges.

### Solutions (kept)
- **Iter 10:** runner trail `trail_atr_mult` 1.0 → **1.5** (ride trends further; floor protects).
- **Iter 12 (the fix):** new `plan.sideways_block_longs` = **True** — drop longs in `side-*`
  regimes. Removed the −70% side-low-long bucket → PnL $70 → **$284**, win 71% → 76%.
- Reverted: stop 1.2 (→1.3), scale-out 0.3 (→0.5).

### Files touched (loop 2)
- `src/ats/config.py` — new `PlanConfig.sideways_block_longs`.
- `src/ats/planning/create_plan.py` — sideways long gate on `allowed_directions`.
- `src/ats/strategy_profiles.py` — scalper: `trail_atr_mult` 1.5, `sideways_block_longs` True
  (stop stays 1.3, scale-out 0.5).

**Tests:** full suite 350 pass, 3 skip, same 2 pre-existing unrelated failures (confirmed by stash).

### Caveat
These thresholds are fit to BTC over Jan–Jun 2026 (net-bearish, so the short book carries the PnL).
The *mechanisms* are general — profit-lock to convert scratches to wins, a 1.3-ATR stop, and
"no counter-trend trades in chop" — but re-validate the values on other symbols/regimes before live.

---

# ⚠️ Entry look-ahead correction (2026-06-21) — Loops 1 & 2 numbers are inflated

**What was found.** The `wick_limit` entry mode cheated. A setup was *detected and filled on the
same closed bar*: the rule engine read that bar's close-derived indicators (RSI/MACD/CVD, plus its
high/low) to authorize the entry, then booked the fill at the zone-edge **wick** price — a price
the bar may have already retraced away from by its close. Both the authorization (closed-bar
indicators) and the touch (intrabar wick) are only knowable *after* the bar closes, yet the fill was
timestamped *inside* that bar. A live system only learns the signal at the close and can never reach
that wick; it would fill at the next bar's open, usually worse.

**Proof it was the whole edge, not a detail.** Flipping `entry_trigger_mode` to honest `close`
fills (decide on the close, fill at that close) **flipped the strategy from profit to a loss.** The
iter8 (+$166) and iter12 (+$284) results — and every win-rate lever tuned to reach them — were
sitting on this artifact. The config comment even advertised it ("realistic and far higher hit
rate"); the hit-rate jump was look-ahead, not skill.

**The fix — arm → pending → fill (a real resting limit order).** `wick_limit` no longer fills on the
deciding bar. It now runs a two-phase lifecycle:

1. **Arm (on bar N's close).** When the thesis gate passes (hard rules + soft score + regime +
   optional confirmation) on the *closed* bar, the setup transitions `active → armed` and a resting
   limit is placed at the conservative zone edge. No fill on N.
2. **Fill (on any bar M > N that trades through the limit).** An armed setup fills purely on price —
   `low ≤ limit ≤ high` — at the limit price, with **no dependence on bar M's close**. Earliest fill
   is N+1 by construction (the arm pass runs after the fill pass), so the deciding bar can never also
   be the fill bar.

This removes both halves of the leak at once: the fill price is genuinely reached by a later bar
(reachable live), and nothing about the fill bar's close authorizes it (no cherry-pick). `close`
mode is unchanged (market-on-close is already executable) and remains the honest baseline. The exit
machinery was already look-ahead-free (stop-wins-ties, drop-the-fill-candle entry-bar reconcile), so
only the entry side needed fixing.

**Files touched**
- `src/ats/engine/orchestrator.py` — `evaluate_now` Phase 1 (fill armed) + Phase 2 (arm/close);
  new `_arm_limit_price()` and shared `_entry_gates_ok()`.
- `src/ats/engine/entries.py` — `execute_setup` takes an explicit `entry_price` (resolved limit /
  close) instead of the `SetupEval`.
- `src/ats/db/models.py` — `Setup.status` gains `armed`.
- `src/ats/engine/state.py`, `src/ats/config.py` — corrected the (previously wrong) "not look-ahead"
  comments to describe the resting-order semantics.
- `tests/test_detector_entries.py` — replaced the same-bar wick-fill test with two: a setup must
  *arm without filling* on its signal bar (even if the bar spans the limit), and an armed setup
  *fills on a later touch* at the limit price.

**What this means for the loop.** Every PnL/win-rate value above was produced under the inflated
fill. **Re-run the iter8 and iter12 configs under the corrected `wick_limit`** to get the honest
baseline, then decide whether the loop's mechanisms (profit-lock, 1.3-ATR stop, no-chop-longs)
survive once entries are realistic. Tests: full suite 359 pass, 3 skip, same 2 pre-existing
unrelated failures (observer-gating + llm-client-default).

---

# LOOP 3 — honest-fills re-baseline (the look-ahead-corrected engine)

**End condition:** `closed_trades >= 300` AND `win_rate >= 60%` AND `pnl_usd >= $200`,
window **`2026-01-01 → 2026-06-19`**, profile `scalper`.

**Honest baseline — `replay_BTCUSDT_15m_20260621-190648.log`: 260t / 67% win / −$623.91.**
After the entry look-ahead fix the strategy is deeply unprofitable. Win rate already clears the
target; the entire problem is PnL (and a small trade-count shortfall).

### Diagnosis — an inverted payoff ratio, not a win-rate problem

| reason | n | sum margin | avg |
|---|---|---|---|
| trail | 156 | +332% | **+2.1%** (≈ +0.27 ATR) |
| tp | 12 | +186% | +15.5% |
| **sl** | **66** | **−740%** | **−11.2%** (≈ −1.3 ATR) |
| expiry | 20 | −114% | −5.7% |

avg winner ≈ +0.27 ATR vs avg loser ≈ −1.3 ATR — a **1 : 4.8** payoff. At 67% win that is still
−1.2%/trade. **Every regime was net-negative despite 70–75% win rates.** Root mechanic: honest
resting-limit fills made winners small, but the +0.2-ATR profit-lock floor + a 1.5-ATR chandelier
(too wide to ever ratchet above the floor before +1.7 ATR) harvested every medium runner back at
+0.2 ATR while losers ran the full 1.3-ATR stop.

## Iter 13 — capture more of each winner (`iter13-tight-trail`) ✅ KEPT
Trail `trail_atr_mult` 1.5 → **0.9** (chandelier now ratchets above the lock from +1.1 ATR on);
profit-lock floor `breakeven_lock_atr` 0.2 → **0.35**. → **264t / 67% / −$440.53** (+$183). Avg
trail win +2.1% → +3.1%; bear-low SHORT −100% → +4%.

## Iter 14 — correct the cost model for limit entries (`iter14-maker-fees`) ✅ KEPT
The entry is a **resting limit = maker fill at the limit price**: maker fee (~2 bps) and **zero**
adverse slippage. The default 5 bps taker + 2 bps slippage on *both* legs (14 bps round-trip)
wrongly modelled the limit entry as a taker market order. Honest round-trip = maker-in (2 bps,
0 slip) + taker-out (5+2) = 9 bps. Set scalper `fee_bps` 5 → **3.5**, `slippage_bps` 2 → **1.0**
(per-leg 4.5 → 9 bps round-trip; exit stays full taker). → **254t / 68% / −$193.66** (+$247).
*A correctness fix, not a tuning knob — the old model charged ~$2/trade of phantom cost.*

## Iter 15 — block sideways shorts (`iter15-block-chop-shorts`) ❌ REVERTED
side-low SHORT was −32% at 34% win, so blocking it *should* have helped. Instead PnL **regressed**
−$194 → −$247. **Lesson: the single-position model is non-local.** Freeing the slot in side-* let
the engine fill different, worse trades and flipped bear-low SHORT +13% → −40% (same ~135 trades,
reshuffled timing). Subtractive regime blocks are unreliable here. New `sideways_block_shorts`
config + gate kept (OFF) for future use. **Reverted.**

## Iter 16 — arm the profit-lock sooner (`iter16-arm-sooner`) ✅ KEPT
`breakeven_arm_atr` 0.6 → **0.45**. The −604% sl bucket is trades that reverse to the full stop
without ever reaching the arm; a trade peaking +0.45..0.6 ATR then reversing is now a +0.35 ATR
**win** instead of a −10% loss. → **281t / 74% / −$25.82** (+$168). Also frees the position slot
faster, so trade count rose 254 → 281.

## Iter 17 — tighten the stop to 1.1 ATR (`iter17-stop-11`) ✅ KEPT
`min_stop_atr_mult` 1.3 → **1.1**. The old "1.3 is optimal" held under look-ahead + a late arm;
with the +0.45 arm shielding genuine winners, tightening mostly shrinks immediate-adverse losers
(−10.4% → −9.5%), lifts RR (1.82), and — since this is also the noise-stop rejection floor —
admits more setups. → **282t / 73% / +$57.84** (+$84). **First positive run.**

## Iter 18 — push the stop to the 1.0 floor (`iter18-stop-10`) ✅ KEPT
`min_stop_atr_mult` 1.1 → **1.0** (sl_tp clamps to max(min, 1.0)). → **286t / 72% / +$86.51** (+$29).

## Iter 19 — lift the profit-lock floor (`iter19-lock-42`) ✅ KEPT
`breakeven_lock_atr` 0.35 → **0.42** (just under the 0.45 arm). The ~190 floor-harvested winners
bank +0.42 instead of +0.35 ATR; the 0.03-ATR give-back still lets runners ride the trail.
→ **295t / 71% / +$141.50** (+$55).

## Iter 20 — modest confidence loosen for volume (`iter20-conf-57`) ✅✅ **ALL CONDITIONS MET**
Book is now solidly +EV, so adding volume helps PnL *and* the count floor. `signal_min_confidence`
0.60 → **0.57** admits the next-best setups as ~+EV trades (NOT the iter6.5 0.50-under-look-ahead
disaster). → **306t / 71% / +$210.90.**

| metric | honest baseline | **iter20** | target |
|---|---|---|---|
| closed | 260 | **306** | ≥ 300 ✓ |
| win rate | 67% | **71%** | ≥ 60% ✓ |
| pnl_usd | −$623.91 | **+$210.90** | ≥ $200 ✓ |
| total margin | −312% | **+106%** | — |

Final book is positive across nearly all regimes (SHORT 225t/+116%, LONG 81t/≈−10%), not a
knife-edge on one bucket. **Loop 3 end condition satisfied.**

---

## Summary — Loop 3 (problems & solutions)

**Start (honest fills): 260t / 67% / −$623.91. End (iter20): 306t / 71% / +$210.90.**

### Problems (root causes)
1. **Inverted payoff ratio.** Win rate was never the issue (67% ≥ target). Winners were harvested
   at +0.27 ATR by a profit-lock floor under a too-wide 1.5-ATR trail, while losers ran the full
   1.3-ATR stop (−11.2%) — a 1:4.8 payoff that made *every regime* net-negative.
2. **Mis-modelled entry costs (a correctness bug).** The cost model charged taker fee + slippage on
   the limit *entry*, but a resting limit is a maker fill at the limit price — zero slippage, lower
   fee. ~$2/trade of phantom cost (~−$500 across the book).
3. **The single-position model is non-local.** Removing a losing regime reshuffles the whole trade
   sequence and can make a *profitable* regime worse (iter15). Subtractive blocks are unreliable;
   universal per-trade levers are robust.
4. **Losers never armed protection.** The −600%+ sl bucket was trades that reversed to the stop
   without ever reaching the +0.6 ATR arm.

### Solutions (kept)
- **Iter 13:** trail 1.5 → **0.9**, lock 0.2 → **0.35** — ratchet/capture more of each winner.
- **Iter 14:** maker-entry cost model — `fee_bps` **3.5**, `slippage_bps` **1.0** (9 bps round-trip).
- **Iter 16:** `breakeven_arm_atr` 0.6 → **0.45** — protect marginal trades before they reverse.
- **Iter 17–18:** `min_stop_atr_mult` 1.3 → **1.0** — shrink immediate-adverse losers (safe now
  that the early arm shields winners) + admit more setups.
- **Iter 19:** `breakeven_lock_atr` 0.35 → **0.42** — bank more on floor-harvested winners.
- **Iter 20:** `signal_min_confidence` 0.60 → **0.57** — +EV volume to clear the 300-trade floor.
- Reverted: iter15 sideways-short block (non-local regression).

### Files touched (loop 3)
- `src/ats/config.py` — new `PlanConfig.sideways_block_shorts` (OFF).
- `src/ats/planning/create_plan.py` — sideways-short gate (companion to the long gate).
- `src/ats/strategy_profiles.py` — scalper: `trail_atr_mult` 0.9, `breakeven_lock_atr` 0.42,
  `breakeven_arm_atr` 0.45, `min_stop_atr_mult` 1.0, `fee_bps` 3.5, `slippage_bps` 1.0,
  `signal_min_confidence` 0.57.

**No look-ahead introduced:** every lever is a risk/exit/cost parameter or a closed-bar plan gate;
the maker-fee change makes costs *more* realistic (entry stays a genuine resting limit, exit stays
full taker). Tests: 359 pass, 3 skip, same 2 pre-existing unrelated failures (observer-gating +
llm-client-default), confirmed unrelated (no observer/LLM code touched).

### Caveat
Values are fit to BTC Jan–Jun 2026 (net-bearish — the short book carries the PnL). The *mechanisms*
generalize (fix the payoff ratio, model maker fills honestly, protect early, don't subtract regimes
under single-position), but re-validate the numbers on other symbols/regimes before live.
