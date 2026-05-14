# Spec 06 — Decision Gate · Milestone M1

> The pivot of the whole project. M1 has built the cheapest possible end-to-end
> deterministic signal (specs 01–04) and a harness that measures it (spec 05).
> This spec defines, **numerically and in advance**, what counts as "the signal
> has edge" — and produces an explicit **go / no-go**.
>
> A green gate unlocks M2. A red gate sends you back into specs 02–04 to fix the
> deterministic layer. It does **not** mean "build M2 anyway and hope the LLM
> rescues it." See `architecture.md` → "The hard truth".
>
> This spec is short on purpose. Its value is that the threshold is written down
> *before* you see the number, so you can't move the goalposts.

---

## Goal

Turn the replay report (spec 05) into a single boolean: **go** or **no-go**, plus
a structured rationale. The thresholds below are the contract. If you want to
change a threshold, change it *in this file in a separate commit before running
the gate* — never after seeing a result.

---

## Milestone & scope

**Milestone:** M1 — Prove Edge. This spec **closes M1.**

**In:**
- A fixed, pre-committed set of pass thresholds
- `ats gate check` — runs the gate against the latest replay and prints go/no-go
- A short `data/gate_<date>.md` rationale artifact

**Out:**
- Anything that changes the signal (that is specs 02–04)
- Anything in M2+

---

## Dependencies on prior specs

- **Spec 05:** a completed `ats replay run` over a ≥ 90d window for the
  ~5-symbol universe, producing the `paper_trades` set and the report.

---

## New deps to add

**None.**

---

## The gate criteria

All criteria are computed from the spec-05 replay over the **most recent 90 days**
of available history for the M1 universe.

| # | Criterion | Threshold | Why this number |
|---|---|---|---|
| G1 | **Sample size** | ≥ 50 closed paper trades | Below this, every other number is noise. Hard fail — not even evaluated further. |
| G2 | **Expectancy beats random** | RR-adjusted expectancy ≥ **+0.10 R** per trade **AND** strictly greater than the random-entry baseline's expectancy by ≥ 0.10 R | Edge means beating a coin flip with the *same* SL/TP/RR structure, not beating zero. |
| G3 | **Hit rate is plausible for the RR** | observed hit rate ≥ the break-even hit rate for the average RR (≈ 1 / (1 + RR)) **plus a 5-point margin** | At RR 2:1 break-even is ~0.33; we require ~0.38+. The margin absorbs slippage and bar-resolution error. |
| G4 | **Confidence is informative** | hit rate is **monotonically non-decreasing** across the `[0.6,0.7)`, `[0.7,0.8)`, `[0.8,1.0]` confidence buckets, each bucket with n ≥ 10 | If higher confidence doesn't mean higher hit rate, the synthesizer's confidence number is decoration. This is the single most important criterion. |
| G5 | **Not regime-fragile** | the signal is positive-expectancy in **at least 2 of the 4** regime cells that have n ≥ 10 | A signal that only works in `bull-low` is a bull-market beta trade, not edge. |
| G6 | **No single-symbol artifact** | no single symbol contributes > 50% of total positive expectancy | Guards against "it's really just the SOL run in March". |

**Result logic:**

- **G1 fails** → `NO-GO (insufficient data)`. Not a verdict on the signal —
  backfill more history or widen the window, then re-run.
- **G1 passes, any of G2–G6 fail** → `NO-GO (no edge)`. The deterministic signal
  does not clear the bar. Go to "If the gate is red" below.
- **All pass** → `GO`. M1 is complete. Proceed to spec 07.

---

## `ats gate check`

```text
ats gate check [--window 90d]        # evaluate the gate against the latest replay; print verdict
ats gate validate                     # programmatic smoke test (runs against a fixture replay)
```

Output:

```
DECISION GATE — 2026-05-13  (replay window 2026-02-12 → 2026-05-13)

G1 sample size            50+ trades        86      PASS
G2 expectancy vs random   +0.10 R & >base   +0.21 R vs -0.01 R   PASS
G3 hit rate vs break-even >= be + 5pts      0.465 vs 0.38        PASS
G4 confidence monotonic   non-decreasing    0.39 < 0.50 < 0.60   PASS
G5 regime breadth         >=2/4 positive    3/4 positive         PASS
G6 no single-symbol >50%  <=50%             BTC 34%              PASS

VERDICT: GO  →  M1 complete. Proceed to spec 07 (M2 — LLM Layer).

rationale written to data/gate_2026-05-13.md
```

`ats gate check` exits `0` on **GO**, `1` on **NO-GO**. The `data/gate_<date>.md`
artifact records every criterion's value and the verdict, so the decision is
auditable later.

---

## Components

| Path | Responsibility |
|---|---|
| `src/ats/gate/criteria.py` | the six criteria as pure functions over a `paper_trades` set; thresholds as module constants |
| `src/ats/gate/verdict.py` | combine criteria → `GO` / `NO-GO` + reason |
| `src/ats/gate/report.py` | render `data/gate_<date>.md` |
| `src/ats/cli/gate.py` | `ats gate check`, `ats gate validate` |

---

## If the gate is red

A red gate is a **normal, expected outcome** — it is the cheap failure the whole
M1 ordering exists to buy. Do **not**:

- build M2 hoping the LLM layer rescues it (the LLM is clamped to ±0.20 — it
  cannot manufacture edge that isn't in the deterministic core)
- tune weights against the replay until it passes (that is overfitting; spec 05
  risks section covers this)

Do, in order:

1. **Read the by-regime and by-confidence breakdowns.** A signal that fails G4
   (confidence not informative) points at the synthesizer; a signal that fails
   G5 (regime-fragile) points at regime modulation or feature selection.
2. **Check the Liquidity proxy.** Spec 04's M1 Liquidity agent is a candle-derived
   stand-in. If `agent_runs` shows it is pure noise, the real liquidation feed
   (an M4 data class) may be the missing input — that is a *data* problem, not an
   *agent* problem.
3. **Question the features, not the orchestration.** Per `architecture.md`
   principle 2 ("data first"), a dead signal is usually a data/feature problem.
4. **Consider that the answer might be "no".** It is legitimate to conclude the
   deterministic signal as specced has no edge and the project pauses here. That
   is a few weeks spent, not a quarter — exactly the trade this milestone
   structure was designed to make.

Each iteration: change specs 02–04, re-run spec 05's replay, re-run `ats gate
check`. Do not re-define the thresholds in this file to make a result pass.

---

## Validation

### Acceptance criteria

- [ ] **Thresholds are constants:** G1–G6 thresholds live in `criteria.py` as named constants, not inline magic numbers
- [ ] **Fixture GO:** a synthetic `paper_trades` set engineered to pass all six → `ats gate check` exits 0, verdict `GO`
- [ ] **Fixture NO-GO (each criterion):** six synthetic sets, each engineered to fail exactly one criterion → verdict `NO-GO` naming that criterion
- [ ] **G1 short-circuit:** a 20-trade set → `NO-GO (insufficient data)`, criteria G2–G6 reported as `not evaluated`
- [ ] **Rationale artifact:** every `ats gate check` writes `data/gate_<date>.md` with all six values and the verdict
- [ ] **Exit codes:** `GO` → exit 0, any `NO-GO` → exit 1

### pytest

| File | Asserts |
|---|---|
| `tests/test_gate_criteria.py` | each criterion function on hand-built fixtures |
| `tests/test_gate_verdict_go.py` | all-pass fixture → GO |
| `tests/test_gate_verdict_nogo.py` | one-fail fixtures → NO-GO naming the criterion |
| `tests/test_gate_g1_shortcircuit.py` | < 50 trades → insufficient-data verdict |

### `ats gate validate`

1. Loads a committed fixture `paper_trades` set (a known-GO and a known-NO-GO)
2. Runs the gate on each; asserts the expected verdict and exit code
3. Asserts the rationale artifact is written
4. Exits 0 on success

---

## Risks / open questions

- **Goalpost-moving.** The entire point of this spec is to prevent it. If a
  threshold genuinely seems wrong, change it in a standalone commit *with
  reasoning*, before the next gate run — never as a reaction to a specific
  failing number.
- **90d may straddle one regime.** Crypto can spend a quarter entirely in one
  regime, weakening G5. If the available history is regime-monotone, note it in
  the rationale and treat a GO as provisional — the forward paper trades in M2
  become the real confirmation.
- **Expectancy vs. drawdown.** The gate measures per-trade expectancy, not
  equity-curve smoothness. A GO with ugly drawdown is still a GO for M1 purposes,
  but flag it — M2's learning loop should watch `max_drawdown_pct`.
