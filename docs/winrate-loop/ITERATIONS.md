# BTC Scalper Win-Rate Optimization Loop

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
</content>
