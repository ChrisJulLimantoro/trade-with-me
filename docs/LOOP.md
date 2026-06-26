# Training Loop — multi-coin, overfit-resistant

This loop tunes the engine against a **basket of coins** and a **strict Train / Validation /
Holdout timeline split**, so improvements that merely memorize one coin or one window are
rejected instead of rewarded.

## Universe & splits (fixed)

- **Coins:** BTCUSDT, ETHUSDT, SOLUSDT
- **Timeframe:** 15m · **Profile:** scalper
- **Timeline split** (warm-up before each `--from` is auto-handled by `_ensure_data`):

| Split                | Window                       | The loop may...                                        |
| -------------------- | ---------------------------- | ------------------------------------------------------ |
| **TRAIN**      | `2025-01-01 → 2025-09-01` | read these logs and tune against them                  |
| **VALIDATION** | `2025-09-01 → 2026-02-01` | grade generalization —**never** diagnose off it |
| **HOLDOUT**    | `2026-02-01 → 2026-06-01` | touch**exactly once**, at the very end           |

`BTCUSDT` must always be backfilled even though all three coins trade — regimes are computed
from BTC-1h only and shared globally, so ETH/SOL replays depend on it.

## Command template

```
uv run ats engine replay --symbol <COIN> --timeframe 15m \
    --from <SPLIT_FROM> --to <SPLIT_TO> --profile scalper --run-label <label>
```

Run the 3 coins of a split concurrently, but **controlled-sleep before each start** so each gets
a distinct `run_id` timestamp.

## The loop

1. **Replay TRAIN** for all 3 coins. Label: `s3-train-i<N>-<COIN>` (N = iteration number).
2. **Diagnose & tune** — reading **TRAIN logs only**. Attack any part of the engine. Do **not**
   open validation/holdout logs to motivate a change; that re-introduces data snooping.
3. **Replay VALIDATION** for all 3 coins. Label: `s3-val-i<N>-<COIN>`. This is the generalization
   check, not a tuning input.
4. **Accept / reject the change:**
   - Accept only if it **holds-or-improves TRAIN gates** AND **does not degrade aggregate
     VALIDATION** (win-rate or expectancy).
   - If TRAIN improves but VALIDATION degrades → overfitting signal → **reject** the change.
5. **Record** in `@ITERATIONS.md`: the change, TRAIN numbers, VALIDATION numbers, and the
   accept/reject decision with rationale. (File does not exist yet; the first iteration creates it.)
6. Go back to step 1 (use the latest logs).

## Gates (strict on TRAIN, relaxed out-of-sample)

- **TRAIN** — per coin, all three must pass:
  `closed_trades ≥ 150` AND `win_rate ≥ 72%` AND `pnl_usd ≥ $400`.
- **VALIDATION** — per coin, all three must pass (shorter window, relaxed bar):
  `closed_trades ≥ 90` AND `win_rate ≥ 62%` AND `pnl_usd > $100`.

**Stop the loop when** all 3 TRAIN coins pass strict AND all 3 VALIDATION coins pass relaxed.

**Abort** if 5 consecutive iterations produce no accepted improvement.

## Final holdout (run once — never tune against it)

When the stop condition is met, replay **HOLDOUT** for all 3 coins
(label: `s3-holdout-final-<COIN>`). Report these numbers as the **honest out-of-sample estimate**.

Do **not** loop back to tune against a holdout failure — that converts the holdout into another
training set. If holdout underperforms validation, record the gap in `@ITERATIONS.md` as the real
generalization cost.

## IMPORTANT CONSTRAINTS

1. NEVER cheat the system — the backtest must be treated like a live system with honest entry and exit.
2. NEVER include any future data (no lookahead bias — that is cheating).
3. If you find any bug that cheats the system, fix it **first**, before applying your next tuning change.
4. The TRAIN/VALIDATION/HOLDOUT wall is part of the honesty contract: a change may only be
   *motivated* by TRAIN data. Looking at validation/holdout to decide what to change is a form of
   cheating, even though it uses no future data within a window.

## At the end

Summarize all findings (problems) and what you changed (solutions) in `@ITERATIONS.md`, and report
the final HOLDOUT numbers alongside the TRAIN/VALIDATION numbers so the generalization gap is visible.
