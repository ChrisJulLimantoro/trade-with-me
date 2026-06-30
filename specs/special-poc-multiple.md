# Spec (Special POC) — Multi-Coin / Multi-Strategy Robustness Harness · future addition

> A future POC that extends the replay harness (spec 05) from single-symbol hit-rate
> measurement to a **multi-coin × multi-strategy** portfolio evaluation, reporting
> **risk-adjusted** performance (Calmar/MAR, max-drawdown, Sharpe, Sortino) out-of-sample,
> and feeding a **regime → strategy-sleeve prior** into the episodic memory (spec 08).
>
> **Status:** proposed / not yet scheduled. Captured from a design discussion; no code written.
>
> **It still measures; it does not tune.** This honours spec 05's anti-goal: there is no
> parameter search and no optimization loop. It evaluates a small fixed set of strategy
> *sleeves* across coins and records descriptive priors. The priors are *memory the
> strategist may read*, never a knob an optimizer turns.

---

## Context — why this exists

The motivating idea: backtest across multiple coins and multiple strategy types to reach a
strong Sharpe, then use that outcome as agent learning material, with leverage applied to
"maximize" the result. That framing was stress-tested and revised:

- **Leverage is not a free Sharpe multiplier.** This repo already models liquidation
  (`reconcile.py:_liq_hit`) and ingests perp funding, so textbook Sharpe-invariance under
  leverage breaks: liquidation is path-dependent ruin and variance drag scales as L².
  Leverage is treated here as a **risk budget** (vol-target + fractional Kelly + drawdown
  breaker), never an amplifier dialed to max.
- **A single in-sample Sharpe over a coin×strategy grid is a multiple-testing artifact.**
  It is only meaningful out-of-sample and after a trial-count haircut (deflated Sharpe).
- **Sharpe is a scalar with no retrieval key.** Existing memory (`learnings`) is
  instance-level, keyed by `fingerprint` + `regime_cell`. Portfolio learning is therefore
  stored at a matching grain: `regime → sleeve`.

**Confirmed design decisions:** north-star = robustness-adjusted (Calmar/MAR + maxDD primary;
Sharpe/Sortino secondary; deflated-Sharpe reported). Validation = walk-forward, OOS-only.
Memory unit = `regime → sleeve` OOS prior. Leverage = vol-target + fractional Kelly with a
drawdown circuit-breaker.

---

## Goal

Given populated `candles` / `features` / `regimes` history for a multi-symbol universe:

1. Define a small fixed set of **strategy sleeves** (orthogonal return drivers), each a
   profile + an allowed setup-category filter.
2. Drive the existing replay loop (`engine/loop.py`) across `symbols × sleeves`.
3. Build per-sleeve and **portfolio** equity curves; compute risk-adjusted metrics.
4. Measure the **realized correlation** of sleeve returns (do not assume diversification)
   and stress-test a correlation→1 crash.
5. Run **walk-forward**: only out-of-sample segments count toward reported metrics and are
   eligible to write memory.
6. Write a `regime → sleeve` OOS prior into episodic memory; inject it into the plan
   envelope alongside the existing `prior_lessons`.

Output: a single command (`ats eval run`) producing a portfolio robustness report.

---

## Milestone & scope

**Milestone:** post-M2 (depends on spec 05 harness + spec 08 learning being in place).

**In:**
- A metrics layer (none exists today): equity curve, Sharpe, Sortino, Calmar/MAR, maxDD,
  turnover, CAGR, deflated Sharpe.
- Strategy "sleeves" = profile + allowed setup-category filter, giving genuinely different
  return drivers (trend / mean-reversion / carry) rather than one factor repeated per coin.
- A harness that drives the existing replay loop across symbols × sleeves and aggregates a
  portfolio curve; a realized sleeve-return correlation matrix; a correlation→1 stress.
- Walk-forward (rolling train/test); OOS-only metrics and OOS-only memory writes.
- Vol-target + fractional-Kelly sizing with a drawdown circuit-breaker, respecting existing
  leverage/margin caps; an optional fixed-leverage sweep (1×/2×/3×) to show variance drag.
- A `sleeve_priors` table and its retrieval, injected into the plan envelope.

**Out (anti-goals — same posture as spec 05):**
- **No parameter search, no weight tuning, no optimization loop.** Sleeves are a fixed set.
- **No "maximize leverage" path.** Leverage is sized by risk budget only.
- No live trading and no always-on service. Offline batch; `MockClient` default (zero API
  cost) so the POC stays idle-cost-zero.
- In-sample numbers may be reported for context but are **never** written to priors.

---

## Dependencies on prior specs

- **Spec 05:** the replay walk and no-look-ahead guarantees (`engine/loop.py`,
  `ReplayReport`, `TradeOutcome`, candle-replay reconciliation).
- **Spec 08:** episodic memory (`learnings`, `fingerprint.py`, `retrieval.py`,
  `post_mortem.py`) — the new sleeve-prior memory mirrors this pattern and reuses the
  plan-envelope injection point.
- **Spec 04 / 07:** the setup catalog (`llm/strategies.md`) categories used to define sleeves.
- **Spec 01/02:** ≥120d candles + features/regimes for the multi-symbol universe (replay
  window + warm-up).

---

## New deps to add

**None expected.** Metrics are pure numpy. The harness is orchestration over the existing
replay loop. One Alembic migration adds one table.

---

## Data model

### sleeve_priors (new)

```
sleeve_priors(
  regime_cell   TEXT NOT NULL,
  sleeve        TEXT NOT NULL,
  oos_calmar    NUMERIC,
  oos_sharpe    NUMERIC,
  oos_maxdd     NUMERIC,
  sample_size   INTEGER NOT NULL,
  updated_at    TIMESTAMPTZ DEFAULT now(),
  UNIQUE (regime_cell, sleeve)
)
```

Migration: `alembic/versions/0005_sleeve_priors.py`. Upsert on `(regime_cell, sleeve)`.

### TradeOutcome (extend)

Add `entry_time` / `exit_time` to `engine/loop.py:TradeOutcome` (source:
`detector.ClosedTradeInfo`) so equity curves can be ordered in time.

---

## Components

New package `src/ats/eval/`:

- `metrics.py` — `equity_curve`, `sharpe`, `sortino`, `calmar`, `max_drawdown`, `turnover`,
  `cagr`, `deflated_sharpe(sharpe, n_trials, n_obs, skew, kurt)`. Explicit annualization
  factor passed in (no hidden √252).
- `sleeves.py` — `Sleeve = {name, profile, allowed_setup_categories}`; filter applied to
  `PlanOutput.allowed_setups` by category before detection.
- `harness.py` — drive replay across `symbols × sleeves`; aggregate per-sleeve + portfolio
  equity curves (equal-risk default, vol-weight optional); sleeve-return correlation matrix;
  correlation→1 stress; optional fixed-leverage sweep.
- `walkforward.py` — rolling train/test windows; OOS-only results feed metrics/memory; trial
  count feeds `deflated_sharpe`.
- `sizing.py` — `vol_target_size`, `fractional_kelly`, `drawdown_breaker`; respects
  `config.py` leverage/margin caps (`max_leverage`, `max_margin_pct_per_trade`,
  `max_total_margin_pct`, `max_portfolio_risk_pct`).
- `sleeve_memory.py` — `write_sleeve_priors(reports)` (OOS upsert) and
  `retrieve_sleeve_prior(session, regime_cell)` (plain keyed lookup, JSON-ready dicts,
  mirroring `retrieval.py`).

Touch points in existing code:
- `planning/create_plan.py` `build_envelope()` — inject `sleeve_priors` next to
  `prior_lessons`, gated by `settings.sleeve_memory_enabled`.
- `config.py` / `strategy_profiles.py` — add `target_vol`, `kelly_fraction`,
  `max_drawdown_breaker_pct`, `sleeve_memory_enabled`, eval defaults.
- CLI entrypoint — register `ats eval`.

---

## CLI added

```
ats eval run --symbols BTCUSDT,ETHUSDT,SOLUSDT --since 90d \
    [--client mock] [--walk-forward] [--leverage-sweep]
# prints: portfolio Calmar/Sharpe/Sortino/maxDD, per-sleeve breakdown,
#         sleeve correlation matrix, leverage sweep, deflated Sharpe

ats eval validate    # smoke test: sleeve_priors table exists and is queryable
```

---

## Validation

### Smoke test

```
# specs 01/02 must have ≥120d of candles/features for the universe
ats db migrate
ats eval validate
ats eval run --symbols BTCUSDT,ETHUSDT,SOLUSDT --since 90d --client mock --walk-forward --leverage-sweep
```

### Acceptance criteria

- Report shows portfolio Calmar/MAR and maxDD as primary, Sharpe/Sortino secondary.
- Deflated Sharpe is reported and is **below** the raw Sharpe when >1 trial was run.
- Sleeve correlation matrix is printed from realized returns (not assumed).
- Leverage sweep shows compounded return degrading (variance drag / liquidation) as leverage
  rises past the vol-targeted level.
- Re-running upserts `sleeve_priors` with no duplicate `(regime_cell, sleeve)` rows.
- A subsequent `ats engine replay` envelope contains a `sleeve_priors` block.
- No optimization/search step exists anywhere in the run (anti-goal preserved).

### pytest

- `tests/test_eval_metrics.py` — golden-value Sharpe/Sortino/Calmar/maxDD on a hand-built
  series; deflated-Sharpe haircut.
- `tests/test_walkforward.py` — train/test split correctness; OOS-only selection.
- `tests/test_sizing.py` — vol-target and fractional-Kelly sizing; drawdown breaker fires.
- `tests/test_sleeve_memory.py` — prior write/read round-trip; upsert uniqueness.

### `ats eval validate`

Programmatic smoke test: the `sleeve_priors` table exists and is queryable (mirrors the
other `*_validate` commands).

---

## Relationship to the spec 05 anti-goal

Spec 05 forbids backtesting-driven optimization. This POC stays inside that boundary:

- Sleeves are a **fixed, curated** set of return drivers — not a search space.
- Walk-forward + deflated Sharpe exist to make reported numbers honest, not to pick winners.
- `sleeve_priors` is **descriptive episodic memory** the strategist may read; it does not
  retune any weights or auto-select strategies. If a future version begins *selecting* sleeves
  by prior, that crosses into optimization and must be re-evaluated against the anti-goal.

---

## Risks / open questions

- **Crypto correlations spike to ~1 in drawdowns**, so backtested diversification overstates
  benefit; the correlation→1 stress is mandatory, not optional.
- **Sleeve definition is the hard part** — sleeves must be genuinely orthogonal or the
  "multiple strategies" claim collapses into one factor. Validate with the realized
  correlation matrix.
- **Funding cost on the levered leg** must be charged in the equity curve, or the vol-target
  result is optimistic.
- **Regime granularity** for priors: too fine → tiny `sample_size` and noisy priors; reuse
  the existing `regime_cell` definition from spec 08 rather than inventing a new one.
