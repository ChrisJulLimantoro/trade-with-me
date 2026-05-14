# Spec 03 — Orchestration · Milestone M1

> Rank the universe, pick the top picks, and manage signal lifecycle so we don't
> spam duplicates. Still no LLM. The operators exposed here are deterministic.
>
> **M1 vs M2 — the diversity filter is deferred.** At the ~5-symbol M1 universe a
> diversity filter is meaningless (you'd never hit a per-category cap). The
> filter design stays in this spec, clearly marked **"Deferred to M2"**, and is
> turned on by spec 07 when the universe expands to ~50. In M1, top-N is a plain
> sort by attention score.
>
> **Tiered:** Tier 1 fires the cycle synchronously inside `ats session run`
> (wrapped as the `/cycle-now` skill in M3). Tier 3 (M4) promotes the same
> `run_cycle()` function to an arq worker on candle-close events — Redis and arq
> are M4-only. Same math, same emitted rows.
>
> **Tier-1 outcome reconciliation** is unique to this spec: before computing a
> new cycle, walk all `active` signals and check candle highs/lows since their
> last update. Close on SL/TP hit (conservative side). This is the session-mode
> substitute for M4's live mark-price poller, and the **replay harness (spec 05)
> reuses this exact walk**.

---

## Goal

On every closed 15m candle:

1. Compute attention score per symbol from normalized features
2. Apply diversity filter (category caps)
3. Persist top-10 of the cycle to `top_picks`
4. Update `signals` state machine — emitting only when a coin is in the top-10
   *and* has no `active` signal already

The "orchestration swarm" in v0.2 is just this: a deterministic scheduler that
fans out to the deep-analysis layer in Phase 4. There are no chatty agents at
this layer.

---

## Milestone & scope

**Milestone:** M1 — Prove Edge.

**In (M1):**
- Attention scoring: weighted blend of `pr_*` features
- Top-N selection by plain descending `attention_score` sort (N defaults to 10)
- `top_picks` history and `signals` lifecycle table
- Tier-1 outcome reconciliation (the candle-replay closer)
- `ats session run` — the M1 master command

**In (M2 — spec 07):**
- Diversity filter using `seeds/categories.yaml` (design below, deferred)

**In (M4 — `specs/10-live-operations.md`):**
- Redis hot cache for "latest top-10"
- arq worker subscribed to candle-close events

**Out:**
- No deep analysis (spec 04)
- No LLM calls (spec 07)
- No SL/TP, no signal directions (spec 04 extends `signals` with those)

---

## Dependencies on prior phases

- **Phase 2**: `features` and `regimes` populated
- **Phase 1**: at least 30d of candles backfilled for the universe

---

## New deps to add

**Tier 1 — none.** The cycle runs synchronously inside `ats session run`. No Redis.

**Tier 3 — opt-in only:**

```bash
uv add 'redis[hiredis]' arq
```

Add Redis to `ops/docker-compose.yml` only when promoting:

```yaml
  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    ports: ["127.0.0.1:6379:6379"]
    volumes: ["redis_data:/data"]
    healthcheck: ...
```

---

## Data model

### top_picks (hypertable)

```sql
CREATE TABLE top_picks (
  cycle_ts          TIMESTAMPTZ NOT NULL,           -- 15m close
  rank              INT NOT NULL,
  symbol            TEXT NOT NULL,
  attention_score   NUMERIC NOT NULL,
  components        JSONB NOT NULL,                 -- {pr_atr, pr_vol_z, ...}
  category          TEXT NOT NULL,
  regime_cell       TEXT NOT NULL,
  PRIMARY KEY (cycle_ts, rank)
);
SELECT create_hypertable('top_picks', 'cycle_ts', if_not_exists => TRUE);
CREATE INDEX idx_top_picks_symbol_ts ON top_picks (symbol, cycle_ts DESC);
```

### signals (the state machine)

```sql
CREATE TABLE signals (
  id                UUID PRIMARY KEY,
  symbol            TEXT NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  proposed_ts       TIMESTAMPTZ NOT NULL,           -- cycle_ts that proposed it
  status            TEXT NOT NULL,                  -- 'proposed' | 'active' | 'expired' | 'invalidated' | 'realized'
  status_reason     TEXT,
  status_changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  attention_score   NUMERIC NOT NULL,
  regime_cell       TEXT NOT NULL
  -- direction, entry_zone, stop_loss, take_profit, confidence, size_pct, reasons[], invalidations[]
  -- are added in Phase 4 via ALTER TABLE
);
CREATE INDEX idx_signals_active ON signals (symbol, status) WHERE status = 'active';
CREATE INDEX idx_signals_status_ts ON signals (status, status_changed_at DESC);
```

---

## Attention scoring

```text
attention_score =
    0.25 * pr_atr                 # volatility
  + 0.25 * pr_vol_zscore          # volume spike
  + 0.20 * pr_momentum            # composite momentum
  + 0.15 * pr_funding_imbalance   # crowded funding
  + 0.15 * pr_oi_delta            # OI buildup
```

All inputs are pre-normalized to `[0, 1]` in Phase 2. The output is in `[0, 1]`.

Rules:
- If any input is NULL (cold-start symbol), the symbol is **excluded** from the cycle
- Weights are constants in `src/ats/orchestration/weights.py` — version-controlled, not config
- They do not change automatically in v0.2; only the weekly reflection in Phase 5 can suggest changes, applied manually

---

## Diversity filter — DEFERRED TO M2 (spec 07)

> **Not built in M1.** At ~5 symbols this filter never binds. The design below is
> the contract spec 07 implements when the universe expands to ~50. In M1,
> `run_cycle()` selects top-N by a plain descending sort on `attention_score`
> with no category logic. Skip to "Signal lifecycle" for the M1 path.

Source: `seeds/categories.yaml` — maintained by hand, refreshed quarterly.

```yaml
btc_major: [BTCUSDT]
eth_ecosystem: [ETHUSDT, LDOUSDT, RPLUSDT]
l1_alt: [SOLUSDT, AVAXUSDT, SUIUSDT, APTUSDT, SEIUSDT, ...]
l2: [ARBUSDT, OPUSDT, MNTUSDT, ...]
defi: [AAVEUSDT, UNIUSDT, ...]
ai: [RNDRUSDT, FETUSDT, TAOUSDT, ...]
meme: [DOGEUSDT, SHIBUSDT, PEPEUSDT, WIFUSDT, ...]
other: [...]   # catch-all
```

Filter algorithm:

```
sort symbols by attention_score desc
results = []
per_category_count = {}
for sym in sorted:
    cat = lookup(sym, default='other')
    if per_category_count[cat] >= MAX_PER_CATEGORY:  # default 2
        continue
    results.append(sym); per_category_count[cat] += 1
    if len(results) == TOP_N: break

# fill any remaining slots from leftover sorted list, ignoring category caps
while len(results) < TOP_N and leftover:
    results.append(leftover.pop(0))
```

This gives diversity-preferred top-N but never under-fills if the universe is
small.

---

## Signal lifecycle

States: `proposed → active → expired | invalidated | realized`

```
on cycle_close(cycle_ts):
    top10 = compute_top_picks(cycle_ts)
    for pick in top10:
        if exists signals where symbol = pick.symbol and status = 'active':
            continue                                            # suppress (dedup)
        emit signal(pick) with status='proposed'                # Phase 4 will fill direction etc.

on phase4_analysis_complete(signal_id):
    if synthesis.confidence >= threshold and rr >= 1:2:
        signal.status = 'active'
    else:
        signal.status = 'expired'; reason='low_confidence'

every 1m:
    for s in signals where status='active':
        # Phase 5 outcome tracker handles realized/invalidated transitions
        if now - s.created_at > 4 * timeframe_minutes:
            s.status = 'expired'; reason='timeout'
```

In v0.3 (Phase 3 only — before Phase 4 exists), every top-pick emits a
`proposed` signal that auto-expires. This is a stub state machine that Phase 4
extends, not replaces.

---

## Tier-1 outcome reconciliation

In live mode (Tier 3), Phase 5's mark-price poller closes trades in near-real-time. In
session mode (Tier 1), the same job is done at the **start** of every session, before any
new cycle runs:

```
on session_start(now):
    for s in signals where status='active':
        bars = SELECT high, low, open_time FROM candles
               WHERE symbol = s.symbol
                 AND timeframe = '15m'
                 AND open_time > s.last_checked_at
                 AND open_time <= now
        for bar in bars:
            # conservative side: long → low first checks SL, high checks TP
            #                    short → high first checks SL, low checks TP
            if conservative_hit(s, bar) == 'sl':
                close(s, exit_price=s.stop_loss, exit_reason='sl', exit_ts=bar.open_time)
                break
            if conservative_hit(s, bar) == 'tp1':
                close(s, exit_price=s.take_profit[0], exit_reason='tp1', exit_ts=bar.open_time)
                break
        else:
            s.last_checked_at = now
            if (now - s.created_at) > 4 * timeframe_minutes:
                close(s, exit_reason='expiry')
```

This is **deliberately conservative**: if both SL and TP fell inside a single bar, we
assume SL hit first. The trader's edge is preserved at the cost of slightly worse hit
rates, which is what we want during validation.

The reconciliation result is written to `paper_trades` (Phase 5) inside the same session.
Closed trades immediately get a post-mortem (Phase 5) if budget allows.

---

## Components

| Path | Responsibility |
|---|---|
| `src/ats/orchestration/weights.py` | constants: weight vector + thresholds |
| `src/ats/orchestration/attention.py` | `score_row(features_row, regime) -> (score, components)` |
| `src/ats/orchestration/diversity.py` | load `categories.yaml`; greedy top-N with caps |
| `src/ats/orchestration/lifecycle.py` | signal state machine; pure functions on a `Signal` row |
| `src/ats/orchestration/cycle.py` | `run_cycle(cycle_ts)` — orchestrates score → diversity → emit |
| `src/ats/orchestration/scheduler.py` | arq worker; subscribes to candle-close trigger |
| `src/ats/cache/redis.py` | `set_top_picks(cycle_ts, picks)`, `get_latest_top()` |
| `src/ats/cli/screen.py` | `ats screen ...` |
| `src/ats/cli/signal.py` | `ats signal ...` |
| `seeds/categories.yaml` | maintained category map |

---

## CLI added

```text
ats session run                         # Tier 1 master command: backfill → process → reconcile → cycle → analyze → journal → exit
ats cycle run --now                     # one-shot cycle only (skips backfill/process); skill twin: /cycle-now
ats screen run                          # alias for `ats cycle run --now` (legacy)
ats screen top                          # read latest top-picks (Redis at Tier 3, Postgres at Tier 1)
ats screen watch                        # [Tier 3] follow: run on every cycle close until SIGINT
ats screen replay --cycle 2026-05-12T14:00Z  # recompute a historical cycle from features

ats signal list [--status active]
ats signal show <id>
ats signal close <id> --reason invalidated

ats screen validate                     # programmatic smoke test
```

### Skill surface

| Skill | CLI twin | Purpose |
|---|---|---|
| `/cycle-now` | `ats cycle run --now` | Force one cycle. Asserts that features are fresh; otherwise refuses with a hint to run `ats session run`. |

`ats session run` is the canonical Tier-1 entry point. It composes (1) ingest backfill,
(2) feature compute, (3) outcome reconciliation, (4) cycle, (5) deep analysis on the
new top-10, (6) journal writes. A single command, exits cleanly.

---

## Validation

### Smoke test

```bash
# Phase 1+2 must be running and have ≥7d of features
docker compose -f ops/docker-compose.yml up -d redis
uv run ats screen run
uv run ats screen top
uv run ats signal list
```

Expected:

```
cycle 2026-05-12T14:00Z   regime=bull-low

rank  symbol     score   category         components
  1   BTCUSDT    0.82    btc_major        pr_atr=0.91 pr_vol=0.78 ...
  2   SOLUSDT    0.79    l1_alt           ...
  3   ARBUSDT    0.74    l2               ...
  4   RNDRUSDT   0.71    ai               ...
  ...

signals  proposed: 10   active: 0   expired: 0   (Phase 4 will activate them)
```

### Acceptance criteria

- [ ] **Score math**: 5 random rows from `top_picks` have `attention_score` matching the formula recomputed by hand (within 1e-6)
- [ ] **Determinism**: `ats screen replay --cycle <T>` produces byte-identical output to the original cycle's `top_picks` row
- [ ] **Diversity**: in 100 historical cycles replayed, no cycle has more than 2 symbols per category
- [ ] **Suppression**: with one BTCUSDT `active` signal, the next 4 cycles do not emit a new BTCUSDT signal
- [ ] **Cold-start exclusion**: a symbol with any NULL `pr_*` is never present in `top_picks`
- [ ] **Cycle latency**: full cycle (universe of 50 symbols) completes in < 2 seconds
- [ ] **State invariants**: at any moment, a symbol has at most one `active` signal
- [ ] **Redis cache parity (Tier 3 only)**: `get_latest_top()` matches the most recent `top_picks` rows by `cycle_ts`
- [ ] **Tier-1 outcome reconciliation correctness**: seed an `active` long signal at `entry=100, sl=98, tp=[103]`, then insert 4 candles where one has `low=97.5`. Running `ats session run` closes the signal with `exit_reason='sl'`, `exit_price=98`, `exit_ts` = that candle's `open_time`.
- [ ] **Tier-1 reconciliation conservatism**: if a single candle has both `low ≤ sl` and `high ≥ tp`, the signal closes with `exit_reason='sl'` (trader's-edge bias).
- [ ] **`/cycle-now` parity**: the skill and `ats cycle run --now` produce byte-identical `top_picks` and `signals` rows when invoked against the same features snapshot.

### pytest

| File | Asserts |
|---|---|
| `tests/test_attention_score.py` | fixture features row → expected score and components |
| `tests/test_diversity_filter.py` | fixture top-15 raw → expected top-10 after cap |
| `tests/test_diversity_undersized.py` | universe of 5 symbols → returns 5 (never blocks under-fill) |
| `tests/test_signal_lifecycle.py` | sequence of transitions; invalid transitions raise |
| `tests/test_cycle_idempotency.py` | re-running `run_cycle(t)` doesn't duplicate `top_picks` rows |

### `ats screen validate`

1. Asserts `features` has ≥ 7d for ≥ 5 symbols
2. Runs `run_cycle()` for the most recent closed 15m
3. Asserts `top_picks` row written; rank 1..10 present
4. Asserts diversity caps held
5. Re-runs once; asserts no duplicate rows
6. Exits 0 on success

---

## Risks / open questions

- **`categories.yaml` rot** — a coin's narrative category changes (e.g. AVAX as L1 vs L2 wrapper). Quarterly review is non-negotiable. Bad category → broken diversity filter.
- **Weight overfit** — published weights are a starting guess. Resist tuning until Phase 5 reflection has data.
- **Universe size vs cycle time** — at 200 symbols the 2s budget may slip. We don't need 200 in v0.2; cap at 50.
- **Redis as critical path** — keep it as a *cache*, never authoritative. Postgres is authoritative; Redis is "what's hot right now".
- **arq vs cron** — arq gives retries and the queue model. For pure "every 15m" we could use a Python `asyncio.sleep(900)` loop, but arq lets Phase 4 enqueue per-symbol jobs cheaply.
