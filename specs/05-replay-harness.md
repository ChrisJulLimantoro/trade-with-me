# Spec 05 — Replay Harness · Milestone M1

> The validation engine. It runs the M1 pipeline (specs 02–04) over **90 days of
> historical candles** and produces a hit-rate report: how many signals would
> have been emitted, how many hit TP vs SL, the RR-adjusted expectancy, and a
> breakdown by regime.
>
> This is **not backtesting-driven optimization** — that is still an anti-goal.
> The harness *measures*; it does not *tune*. You replay to answer one question:
> "does the deterministic signal carry edge?" — and you answer it in an
> afternoon instead of waiting three months for forward paper trades to
> accumulate.
>
> The harness is the input to the decision gate (spec 06).

---

## Goal

Given a populated `candles` / `features` / `regimes` history:

1. Walk every closed 15m cycle timestamp in the window
2. At each timestamp, run `run_cycle()` (spec 03) → `analyze top picks` (spec 04)
   → emit signals **as of that timestamp only** (strict no-look-ahead)
3. Journal each emitted signal as a paper trade
4. Reconcile each open paper trade forward using the candle-replay closer
   (spec 03's Tier-1 reconciliation logic) until it hits SL / TP / expiry
5. Aggregate the closed trades into a hit-rate report

The output is a single command (`ats replay run`) that turns the entire M1
pipeline into a measurable artifact.

---

## Milestone & scope

**Milestone:** M1 — Prove Edge.

**In:**
- A historical cycle walker with hard no-look-ahead guarantees
- Reuse of spec 03's `run_cycle()` and Tier-1 candle-replay reconciliation
- Reuse of spec 04's deterministic synthesizer (eight agents)
- A `paper_trades` table (the same shape spec 08 will reuse for the live loop)
- A hit-rate report: overall, by regime, by confidence bucket, with sample size
  and a random/buy-and-hold baseline for comparison
- **A per-agent ablation matrix**: re-runs the same replay window 8 times, each
  with one agent's weight set to 0 (and the remaining weights renormalized), and
  reports the delta vs baseline. The decision gate (spec 06) reads this matrix.
- `data/replay_<window>/` file artifacts for human audit

**Out:**
- No weight tuning, no parameter search, no optimization loop (anti-goal)
- **The ablation matrix is not weight tuning.** It measures the marginal
  contribution of each agent at its *current* weight. The gate may *drop* an
  agent whose contribution is ≤ 0; it may never *retune* the survivors. There
  is no search over weight values.
- No LLM (M1 is deterministic; the harness re-runs in M2 once spec 07 lands, but
  that is an M2 use of the same tool)
- No live outcome tracking — that is spec 08

---

## Dependencies on prior specs

- **Spec 04:** the deterministic synthesizer and four agents
- **Spec 03:** `run_cycle()`, the signal state machine, and the Tier-1
  candle-replay reconciliation walk
- **Spec 02:** `features` and `regimes` populated for the full replay window
- **Spec 01:** **≥ 120 days** of `candles` backfilled for the ~5-symbol universe
  (90d replay window + 30d warm-up for percentile-rank normalization)

---

## New deps to add

**None.** The harness is orchestration over code that already exists in specs
02–04. It adds a CLI module and one table.

---

## Data model

### paper_trades

This is the canonical paper-trade table. Spec 05 populates it via replay; spec
08 populates it via the live session loop. Same schema, same `close_paper_trade()`
function, so replay-closed and live-closed trades are directly comparable.

```sql
CREATE TABLE paper_trades (
  id                       UUID PRIMARY KEY,
  signal_id                UUID NOT NULL REFERENCES signals(id),
  symbol                   TEXT NOT NULL,
  direction                TEXT NOT NULL,
  entry_price              NUMERIC NOT NULL,
  entry_ts                 TIMESTAMPTZ NOT NULL,
  stop_loss                NUMERIC NOT NULL,
  take_profit              NUMERIC[] NOT NULL,
  size_pct                 NUMERIC NOT NULL,
  status                   TEXT NOT NULL,         -- 'open' | 'closed'
  exit_price               NUMERIC,
  exit_ts                  TIMESTAMPTZ,
  exit_reason              TEXT,                   -- 'sl' | 'tp1' | 'tp2' | 'expiry'
  pnl_pct                  NUMERIC,
  max_drawdown_pct         NUMERIC,
  max_favorable_pct        NUMERIC,
  time_to_exit_minutes     INT,
  regime_cell              TEXT NOT NULL,
  setup_snapshot           JSONB NOT NULL,         -- agent_scores + features + regime at entry
  source                   TEXT NOT NULL DEFAULT 'replay'  -- 'replay' | 'session' | 'live'
);
CREATE INDEX idx_pt_status ON paper_trades (status);
CREATE INDEX idx_pt_symbol_entry ON paper_trades (symbol, entry_ts DESC);
CREATE INDEX idx_pt_source ON paper_trades (source);
```

> `narrative_ids` is added by spec 08 (M2) once narratives exist.

No other tables. The hit-rate report is computed on the fly from `paper_trades`;
spec 06's gate result is also computed live (not persisted) so it can never go
stale.

---

## The replay walk (no-look-ahead is the whole game)

```
ats replay run --since 90d:
    window = [now - 90d, now]
    cycles = every closed 15m open_time in window
    for cycle_ts in cycles:                       # strictly ascending
        # everything below sees ONLY data with open_time <= cycle_ts
        top = run_cycle(cycle_ts, as_of=cycle_ts)
        for pick in top:
            signal = synthesize(analyze(pick, as_of=cycle_ts))
            if signal and signal.status == 'active':
                pt = journal_paper_trade(signal, source='replay')
        # reconcile any paper trade still open, walking candles
        # with open_time > pt.entry_ts and open_time <= cycle_ts
        reconcile_open_trades(as_of=cycle_ts)
    # at end of window, force-close anything still open at last candle
```

**Hard rules:**

- `as_of` is threaded through every query. A feature, candle, or funding row with
  `open_time > as_of` is invisible. A test seeds a future candle and asserts it
  never influences a signal.
- Reconciliation reuses spec 03's **conservative** rule: if a single candle has
  both `low ≤ sl` and `high ≥ tp`, the trade closes as `sl`.
- The walk is deterministic: same candle history + same `SEED` → byte-identical
  `paper_trades`.

---

## The hit-rate report

`ats replay report` (and the tail of `ats replay run`) prints:

```
Replay window: 2026-02-12 → 2026-05-13  (90d, ~5 symbols)

signals emitted        : 86
trades closed          : 86      (0 still open)
  tp1 / tp2            : 31 / 9
  sl                   : 38
  expiry               : 8

hit rate (tp / closed) : 0.465
RR-adjusted expectancy : +0.21 R per trade
avg winner / avg loser : +2.3% / -1.4%

baseline (random entry, same SL/TP/RR) : hit rate 0.33, expectancy -0.01 R
baseline (buy-and-hold BTC, same window): +6.1%

by regime:
  bull-low   : n=24  hit 0.58  exp +0.44 R
  bull-high  : n=19  hit 0.47  exp +0.18 R
  bear-high  : n=21  hit 0.33  exp -0.12 R
  side-low   : n=22  hit 0.45  exp +0.09 R

by confidence bucket:
  0.60-0.70  : n=41  hit 0.39
  0.70-0.80  : n=30  hit 0.50
  0.80-0.90  : n=15  hit 0.60
```

The **baseline rows are non-negotiable** — a hit rate means nothing without the
random-entry comparison. The confidence-bucket monotonicity (higher confidence →
higher hit rate) is the single best smell test for "the score means something".

File artifacts written to `data/replay_<window>/`:

```
data/replay_2026-02-12_2026-05-13/
  trades.csv             # every paper_trade, flat
  report.md              # the report above, as markdown
  by_regime.json
  by_confidence.json
  ablation.md            # the ablation matrix (see next section)
  ablation.json          # machine-readable ablation deltas
```

---

## Per-agent ablation matrix

After the baseline replay completes and writes `paper_trades`, the harness reruns
the **same** cycle walk N additional times (N = number of agents = 8 in M1). On
each rerun, one agent's weight is set to 0 and the remaining weights are
renormalized to sum to 1.0 (arithmetic division — *not* a search). The reruns are
deterministic on the same candle history.

For each rerun, the harness recomputes the same headline metrics (hit rate,
expectancy, signals_count) and reports the **delta vs baseline**.

```
ats replay run --since 90d --ablate           # baseline + 8 ablation runs
ats replay report                              # prints the table below
```

Output (printed at the tail of the report, also written to `ablation.md`):

```
ABLATION MATRIX (90d, ~5 symbols, baseline = all 8 agents)

agent           hit_rate_delta   expectancy_delta   signals_delta   verdict
structure         -3.1%             -0.18 R            -2            earns weight
momentum          -1.4%             -0.07 R            -1            earns weight
funding           -0.6%             -0.03 R             0            earns weight
liquidity         +0.1%             +0.01 R             0            CANDIDATE TO DROP
price_action      -2.2%             -0.12 R            -3            earns weight
cross_venue       -2.8%             -0.15 R            -1            earns weight
basis             -0.9%             -0.05 R             0            earns weight
cvd               -0.3%             -0.02 R             0            earns weight (marginal)
```

**How to read this.** A *negative* delta means removing that agent made the
signal *worse* (the agent earned its weight). A *non-negative* delta means the
agent contributed nothing or hurt — it is flagged `CANDIDATE TO DROP` and the
decision gate (spec 06) may drop it before declaring GO.

**This is measurement, not optimization.** Concretely:

- The harness never searches over weight values. Each rerun uses the *fixed*
  current weights minus the ablated agent's weight (renormalized).
- The "drop" decision is binary (keep or drop), not a continuous tuning knob.
- After a drop, the surviving weights are again *arithmetically* renormalized,
  never re-discovered.
- The matrix is computed once per replay run. Repeated runs against the same
  candle history produce byte-identical matrices.

The ablation runs cost ~8× the baseline replay's CPU; on the M1 ~5-symbol
universe this is still seconds-to-minutes total. The cost grows linearly with
agent count, which is fine for the M1 8-agent set and remains tolerable through
M2 (~10 agents with sentiment + narrative added). Beyond that, sampling
strategies become worth considering — out of scope here.

---

## Components

| Path | Responsibility |
|---|---|
| `src/ats/replay/walker.py` | the no-look-ahead cycle walk; `as_of` threading |
| `src/ats/replay/journal.py` | `journal_paper_trade()` — writes a `paper_trades` row |
| `src/ats/replay/reconcile.py` | thin wrapper over spec 03's candle-replay closer; `close_paper_trade()` |
| `src/ats/replay/report.py` | aggregations + baselines → `report.md` + JSON |
| `src/ats/replay/ablation.py` | runs the per-agent ablation loop; writes `ablation.md` + `ablation.json` |
| `src/ats/cli/replay.py` | `ats replay run [--ablate]`, `ats replay report` |

`close_paper_trade()` lives here and is **reused unchanged by spec 08** — so a
replay-closed trade and a live-closed trade are the same kind of object.

---

## CLI added

```text
ats replay run --since 90d [--symbols ...] [--ablate]   # walk history, journal + reconcile paper trades; --ablate adds the 8-agent ablation matrix
ats replay report [--window <id>]                       # re-print the report for a completed replay (incl. ablation if present)
ats replay validate                                     # programmatic smoke test
```

---

## Validation

### Smoke test

```bash
# spec 01 must have ≥ 120d of candles; spec 02 features backfilled
uv run ats replay run --since 90d
# → prints the hit-rate report; writes data/replay_<window>/
uv run ats replay report
```

### Acceptance criteria

- [ ] **No look-ahead:** a test seeds a candle with `open_time` 1 day in the future relative to a cycle; the signal emitted at that cycle is byte-identical to a run without the future candle
- [ ] **Determinism:** `ats replay run --since 90d` run twice on the same candle history produces byte-identical `paper_trades` rows
- [ ] **Reconciliation conservatism:** a fixture trade where one candle has both `low ≤ sl` and `high ≥ tp` closes as `sl`
- [ ] **Closer parity:** a fixture trade reconciled by the replay closer and the same trade closed by spec 03's session reconciliation produce the same `exit_reason` and `exit_price`
- [ ] **Report completeness:** the report always includes the random-entry baseline and a by-regime breakdown; missing either fails the test
- [ ] **Sample-size honesty:** if the window produces < 30 closed trades, the report prints a prominent "INSUFFICIENT SAMPLE" banner (the decision gate, spec 06, will hard-fail on it)
- [ ] **Coverage:** every emitted `active` signal in the window has exactly one `paper_trades` row; every `paper_trades` row reaches `status='closed'`
- [ ] **Ablation determinism:** `ats replay run --since 90d --ablate` run twice on the same candle history produces byte-identical `ablation.json`
- [ ] **Ablation arithmetic:** in each ablation rerun, `sum(active_weights) == 1.0` (renormalized correctly); a unit test asserts the renormalization on a fixed weight set
- [ ] **Ablation only drops, never tunes:** a CI test asserts the harness never writes a weight value to `src/ats/orchestration/weights.py`; the ablation report only produces deltas + the binary "CANDIDATE TO DROP" label

### pytest

| File | Asserts |
|---|---|
| `tests/test_replay_no_lookahead.py` | future candle never influences a past cycle |
| `tests/test_replay_determinism.py` | two runs → byte-identical paper_trades |
| `tests/test_replay_reconcile_conservatism.py` | sl-and-tp-same-bar → sl |
| `tests/test_replay_closer_parity.py` | replay closer == spec 03 session closer |
| `tests/test_replay_report.py` | fixture trade set → expected report numbers + baselines present |
| `tests/test_replay_ablation.py` | fixture replay → 8 ablation rows; one row's weight set to 0 → expected renormalized weights; positive delta → `CANDIDATE TO DROP` label |
| `tests/test_replay_ablation_no_tune.py` | running `--ablate` does not write to `src/ats/orchestration/weights.py` (file mtime unchanged) |

### `ats replay validate`

1. Asserts `features` covers ≥ 90d for the universe
2. Runs `ats replay run` over a short fixed window (e.g. 14d)
3. Asserts `paper_trades` written, all closed, report generated with baselines
4. Re-runs; asserts byte-identical output
5. Exits 0 on success

---

## Risks / open questions

- **The temptation to tune.** The first replay will produce a number you don't
  like, and the instinct will be to adjust weights until it improves. **That is
  overfitting and it is an anti-goal.** The harness measures; spec 08's
  reflection (with real forward data) is the only thing that authorizes weight
  changes. If you must explore, do it openly and treat the result as a hypothesis
  to be confirmed forward — never as a validated config.
- **The temptation to misuse the ablation matrix.** The matrix exists for one
  decision only: should an agent with ≤ 0 marginal contribution be dropped?
  Looking at the matrix and inventing a new weight ("CrossVenueFlow contributed
  -2.8% — let's raise its weight to 0.20!") is exactly the overfitting the
  anti-goal forbids. The harness enforces this structurally: it produces deltas
  and a binary `CANDIDATE TO DROP` flag, and does not surface any "suggested
  weight" field. Adding such a field is itself a violation.
- **Fill assumptions.** Replay assumes immediate fill at `entry_price`. Apply an
  optional 5 bps slippage haircut for honest accounting; document the limitation.
- **Bar-resolution outcomes.** 15m candles can't tell you intra-bar ordering. The
  conservative SL-first rule handles this; the report should note that real
  forward results may differ slightly (usually in your favor).
- **Survivorship.** The ~5-symbol M1 universe is hand-picked majors that
  obviously survived. That is fine for M1 (we are testing *signal mechanics*, not
  universe selection); revisit when the universe expands in spec 07.
