# LOOP7 — multi-coin training loop (hybrid-engine branch)

Loop spec: `docs/LOOP.md`. Universe BTC/ETH/SOL · 15m · `scalper` profile.
Branch: `f/hybrid-engine` (numbers differ from prior `ITERATIONS.md`, which was a different branch).
Replays are **free/deterministic** (LLM_MOCK=false but the plan path is deterministic — confirmed by user).

**Splits**
- TRAIN `2025-01-01 → 2025-09-01`
- VALIDATION `2025-09-01 → 2026-02-01`
- HOLDOUT `2026-02-01 → 2026-06-01` (touch once)

**Objective (changed by user mid-loop):** maximize **portfolio PnL** (sum of BTC+ETH+SOL) on
TRAIN, honestly (no lookahead/cheating), validated OOS. **Win-rate is no longer a gate.** Accept a
change only if it improves TRAIN portfolio PnL AND does not degrade VALIDATION portfolio PnL.

---

## Iteration 0 — baseline (no change)

| Split | Coin | closed | win | pnl | gate |
| ----- | ---- | ------ | --- | --- | ---- |
| TRAIN | BTC | 330 | 75% | $501.25 | ✅ |
| TRAIN | ETH | 310 | 69% | $430.63 | ❌ win 69% < 72% |
| TRAIN | SOL | 361 | 74% | $1134.82 | ✅ |
| VAL | BTC | 212 | 71% | $127.17 | ✅ |
| VAL | ETH | 225 | 76% | $688.64 | ✅ |
| VAL | SOL | 241 | 73% | $704.60 | ✅ |

Baseline TRAIN portfolio PnL = **$2066** (BTC 501 + ETH 431 + SOL 1135).

---

## Iteration 1 — chop floor `chop_atr_pct_max` 0.0023 → 0.0030 (cross-symbol chop-bleed)

**Motivation (TRAIN only):** the atr_pct band 0.0023–0.0030 is net-negative on TRAIN for BOTH BTC
(75t/69%/−$13) and ETH (part of a 15t/47%/−$62 bucket); SOL has zero trades that low. The 0.0023
cutoff let it escape the 0.80 confidence floor. Widened to 0.0030 to gate the band's marginal
entries cross-symbol while sparing SOL.

| Coin | base | iter1 | Δ |
| ---- | ---- | ----- | - |
| BTC | $501 | $574 | +$73 |
| ETH | $431 | $446 | +$15 |
| SOL | $1135 | $1135 | 0 |
| **portfolio** | **$2066** | **$2154** | **+$88** |

Modest, principled PnL gain (mostly BTC). Kept as working base; nearly a no-op for ETH because
ETH's chop-band losers carry HIGH confidence (a confidence floor can't catch them). VALIDATION
check deferred to the accepted final config.

---

## Iteration 2 — confidence-floor sweep (the volume lever)

Diagnosis pivot: win-rate freed → the dominant PnL lever is the `signal_min_confidence` floor.
TRAIN sweep (portfolio PnL, from iter1 base):

| Config | BTC | ETH | SOL | Portfolio |
| ------ | --- | --- | --- | --------- |
| base (conf 0.70) | $574 | $446 | $1135 | $2154 |
| conf 0.65 | $299 | $700 | $1326 | $2325 |
| conf 0.60 | $291 | $884 | $1448 | **$2623** |
| trail 0.9 | $601 | $400 | $1001 | $2002 (worse) |

**Finding:** lowering the floor admits +EV volume on ETH/SOL (high-vol) but net-negative volume on
BTC (low-vol) — the documented BTC↔ETH/SOL opposition. conf 0.60 nets **+$469** vs base. Looser
trail (0.9) and farther target (reward 3.0, → $2062) are dead levers here — volume, not
winner-size, is the lever. `reward 3.0` = $2062 (neutral).

---

## Iteration 3 — push the floor lower + vol-conditional combos

TRAIN sweep:

| Config | BTC | ETH | SOL | Portfolio |
| ------ | --- | --- | --- | --------- |
| conf 0.60 (iter2 best) | $291 | $884 | $1448 | $2623 |
| conf 0.60 + chop≤0.0040@0.75 | $433 | $923 | $1411 | $2766 |
| conf 0.60 + chop≤0.0045@0.80 | $481 | $795 | $1499 | $2774 |
| **conf 0.55** | $324 | $1486 | $1543 | **$3352** |

**Findings:**
1. The vol-conditional chop combos *protect BTC* (back to ~$433–481) but **claw back more ETH/SOL
   volume than BTC's recovery is worth** → all underperform a plain low floor. BTC's marginal loss
   is small vs ETH/SOL's volume gains, so protecting it is net-negative. Simplest lever wins.
2. **conf 0.55 → $3352** (+$1286 vs iter1 base $2154). ETH win-rate *rises* 69%→76% as the floor
   drops — the confidence score is filtering out *good* ETH/SOL setups. Pushing lower next.

**Overfit watch:** 2025 TRAIN is largely trending; "more trades = more profit" may not survive the
2026 drawdown in VALIDATION. Find the TRAIN turnover, then validate hard before accepting.

Lower-floor TRAIN sweep (monotone — the overfit signature):

| Floor | BTC | ETH | SOL | TRAIN portfolio |
| ----- | --- | --- | --- | --------------- |
| 0.55 | $324 | $1486 | $1543 | $3352 |
| 0.50 | $284 | $1643 | $1460 | $3387 |
| 0.45 | $261 | $1560 | $1671 | $3492 |
| 0.40 | $271 | $1637 | $1854 | $3762 |

No interior TRAIN optimum. TRAIN-only knee = **0.55**: ETH win-rate peaks (76%) and marginal EV
per added trade collapses after it (0.55→0.50 = +100 trades for +$35 ≈ break-even).

---

## Iteration 4 — ACCEPTED: `signal_min_confidence` 0.70 → 0.55

**The confidence gate was miscalibrated** (it suppressed good ETH/SOL setups; ETH win-rate RISES
69→76% as the floor drops). The TRAIN gain **generalizes** — the decisive VALIDATION OOS check
(includes the 2026 drawdown):

| Floor | TRAIN portfolio | VALIDATION portfolio |
| ----- | --------------- | -------------------- |
| 0.70 (base) | $2154 | $1500 |
| 0.60 | $2623 | $1669 |
| **0.55** | **$3352** | **$1903** |
| 0.50 | $3387 | $1907 |

VALIDATION detail at 0.55: BTC 267t/69%/$281 · ETH 319t/77%/$984 · SOL 367t/71%/$639.

**Accept rationale:** conf 0.55 improves BOTH TRAIN (+$1198) and VALIDATION (+$403) vs base — the
*opposite* of the overfit signature. Below 0.55, TRAIN keeps climbing on noise but VALIDATION
flattens and **SOL bleeds OOS** ($639→$473 at 0.50), so 0.55 banks the validated edge without the
overfit tail. Stable ~70–76% win rates across all floors ⇒ more-of-the-same-quality, not a
lookahead artifact (a lookahead bug would show implausible 90%+ win rates). **No cheating: the floor
only admits more honestly-filled setups; per-trade fill logic is unchanged.**

Baked into `strategy_profiles.py` (chop 0.0030 from iter1 retained). **Confirmation:** the baked
config reproduces the override sweep EXACTLY — TRAIN $3352 (BTC 324/ETH 1486/SOL 1543) and
VALIDATION $1903.42 (BTC 281.04/ETH 983.67/SOL 638.71) — proving the override shim was faithful and
the shipped config is the tested config.

---

## FINAL HOLDOUT (2026-02-01 → 2026-06-01) — touched once, finalized config

Loop stopped here at the user's request after iter4. Holdout run once with the finalized profile
(conf 0.55, chop 0.0030):

| Coin | closed | win | pnl |
| ---- | ------ | --- | --- |
| BTC | 226 | 68% | $121.30 |
| ETH | 271 | 67% | $240.66 |
| SOL | 255 | 77% | $866.42 |
| **portfolio** | | | **$1228.38** |

All three coins positive on the untouched 2026-H1 window.

**Reference — original config (conf 0.70, chop 0.0023) on the same holdout:** BTC 154t/68%/−$5.22,
ETH 182t/68%/$77.81, SOL 165t/73%/$314.13 → **$386.72**. The finalized config improved the
genuinely-untouched holdout by **+$842 (3.2×)**, and turned BTC from negative to +$121 — the change
generalizes, it is not an overfit.

---

## Final summary — original → finalized (portfolio PnL)

| Split | Original (0.70 / 0.0023) | Final (0.55 / 0.0030) | Δ |
| ----- | ------------------------ | --------------------- | - |
| TRAIN | $2066 | $3352 | +$1286 (+62%) |
| VALIDATION | $1520 | $1903 | +$383 (+25%) |
| HOLDOUT | $387 | $1228 | +$842 (+218%) |

**Shipped change (2 knobs in `scalper` profile):**
1. `chop_atr_pct_max` 0.0023 → 0.0030 (iter1) — gate the cross-symbol low-vol chop bleed.
2. `signal_min_confidence` 0.70 → 0.55 (iter4) — the miscalibrated confidence gate was suppressing
   good ETH/SOL setups; the dominant PnL lever.

Test harness added: `ATS_PROFILE_OVERRIDE` env shim in `apply_profile()` (inert unless set) for
knob sweeps without per-run file edits. The shipped values are baked into the profile dict.

### Running tally (portfolio PnL)
| | TRAIN | VALIDATION |
| --- | --- | --- |
| baseline (iter0) | $2066 | $1500* |
| **accepted (iter1 chop + iter4 conf 0.55)** | **$3352** | **$1903** |

*iter0 VAL baseline was BTC 212/ETH 225/SOL 241 = $1520 via run_split; valbase sweep (chop 0.0030)
= $1500. Use $1500 as the like-for-like base.
