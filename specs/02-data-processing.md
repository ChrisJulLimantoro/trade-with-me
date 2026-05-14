# Spec 02 — Data Processing · Milestone M1

> Turn raw candles + derived data into normalized features ready for scoring.
> Tag every bar with the prevailing market regime. No scoring, no agents —
> just feature engineering.
>
> **M1 universe:** the same ~5 majors as spec 01. The cost note about "50 symbols
> × 3 tf" in Risks applies only once the universe expands in M2 (spec 07).
>
> **Tiered:** same math regardless of tier. Tier 1 fires the compute synchronously
> inside `ats session run` against whatever candles the backfill just produced.
> Tier 3 (M4) subscribes to candle-close events and computes incrementally.
> Feature rows are byte-identical across tiers for identical candle data.

---

## Goal

For every closed candle on every supported timeframe:

1. Compute deterministic indicators (ATR, RSI, MACD, EMA, OBV, volume z-score, OI delta, funding z-score, realized vol)
2. Normalize each component to `[0, 1]` via rolling percentile rank within the symbol
3. Persist to `features` keyed on `(symbol, timeframe, open_time)`

In parallel, every hour, compute the **global regime** (BTC trend × volatility
percentile → 6 cells) and persist to `regimes`.

---

## Scope

**In:**
- Pure-function indicator engine on top of `pandas-ta`
- Rolling percentile-rank normalization (30d and 90d lookbacks)
- Regime detector (BTC EMA slope × BTC realized vol percentile)
- Continuous computation triggered by closed-candle events from Phase 1
- One-shot historical backfill of features from existing candles

**Out:**
- No screening or ranking (Phase 3)
- No deep analysis (Phase 4)
- No LLM calls
- No new exchange connections

---

## Dependencies on prior phases

- **Phase 1**: `candles`, `funding_rates`, and `open_interest` populated.
  Feature backfill requires at least 90 days of `candles` per symbol for
  percentile-rank windows to be meaningful.

---

## New deps to add

```bash
uv add pandas numpy pandas-ta
```

No infra changes.

---

## Data model

### features (hypertable, one row per closed candle)

```sql
CREATE TABLE features (
  symbol               TEXT NOT NULL,
  timeframe            TEXT NOT NULL,
  open_time            TIMESTAMPTZ NOT NULL,
  -- raw indicators
  atr_14               NUMERIC,
  atr_pct              NUMERIC,                  -- atr_14 / close
  rsi_14               NUMERIC,
  ema_20               NUMERIC,
  ema_50               NUMERIC,
  ema_200              NUMERIC,
  macd                 NUMERIC,
  macd_signal          NUMERIC,
  macd_hist            NUMERIC,
  obv                  NUMERIC,
  vol_zscore_20        NUMERIC,
  oi_delta_pct_1h      NUMERIC,
  oi_delta_pct_24h     NUMERIC,
  funding_rate         NUMERIC,
  funding_z_30d        NUMERIC,
  realized_vol_30d     NUMERIC,
  -- composite (deterministic)
  momentum_composite   NUMERIC,                  -- blend of RSI/MACD/ROC
  -- normalized: percentile rank within symbol over the last 30d
  pr_atr               NUMERIC,
  pr_rsi               NUMERIC,
  pr_vol_zscore        NUMERIC,
  pr_oi_delta          NUMERIC,
  pr_funding_imbalance NUMERIC,
  pr_momentum          NUMERIC,
  -- metadata
  computed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  feature_version      INT NOT NULL DEFAULT 1,
  PRIMARY KEY (symbol, timeframe, open_time)
);
SELECT create_hypertable('features', 'open_time', if_not_exists => TRUE);
```

### regimes (one row per BTC 1h close)

```sql
CREATE TABLE regimes (
  ts                    TIMESTAMPTZ PRIMARY KEY,
  btc_ema_slope_30d     NUMERIC NOT NULL,
  btc_realized_vol_30d  NUMERIC NOT NULL,
  vol_percentile_180d   NUMERIC NOT NULL,        -- 0..1
  trend                 TEXT NOT NULL,           -- 'bull' | 'bear' | 'side'
  volatility            TEXT NOT NULL,           -- 'high' | 'low'
  regime_cell           TEXT NOT NULL,           -- e.g. 'bull-high'
  computed_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## Components

| Path | Responsibility |
|---|---|
| `src/ats/processing/indicators.py` | Pure functions: `atr(df, n=14)`, `rsi(df, n=14)`, `macd(df)`, `ema(df, n)`, `obv(df)`, `realized_vol(df, n=30)`. All take a DataFrame, return a Series. No DB. |
| `src/ats/processing/normalize.py` | `percentile_rank(series, lookback)` — robust to fat tails; uses `closed` window only |
| `src/ats/processing/composite.py` | `momentum_composite(features_row) -> float` blending RSI/MACD/ROC |
| `src/ats/processing/features.py` | Orchestrator: for `(symbol, tf, open_time)` read candles+derived → compute → upsert features row |
| `src/ats/processing/regime.py` | BTC slope + vol percentile → regime cell; upserts `regimes` |
| `src/ats/processing/scheduler.py` | Subscribes to ingestion candle-close events (file-based marker or shared Redis pub/sub later); triggers compute |
| `src/ats/cli/process.py` | `ats process ...` |
| `src/ats/cli/regime.py` | `ats regime show` |

---

## Algorithms

### Percentile rank (the only normalization)

```python
def percentile_rank(series: pd.Series, lookback: int) -> pd.Series:
    """Rolling percentile rank over the previous `lookback` closed bars.
    Output ∈ [0, 1]. Current bar is never in its own window."""
    return series.rolling(lookback, closed='left').rank(pct=True)
```

Lookback per timeframe (closed bars):
- 15m: 2880 bars (≈ 30 days)
- 1h:   720 bars (≈ 30 days)
- 4h:   180 bars (≈ 30 days)

### Composite momentum

```python
def momentum_composite(rsi: float, macd_hist: float, roc_5: float) -> float:
    # normalize to [0, 1] then weight
    rsi_n = (rsi - 30) / 40                          # 30..70 → 0..1
    macd_n = 0.5 + 0.5 * np.tanh(macd_hist * scale)
    roc_n = 0.5 + 0.5 * np.tanh(roc_5 * 10)
    return 0.5*rsi_n + 0.3*macd_n + 0.2*roc_n
```

### Regime detection

- `btc_ema_slope_30d`: slope of EMA-30 over the last 30 closed 1h bars, in % per day
- `trend`: `bull` if slope > +0.5%/d, `bear` if < -0.5%/d, else `side`
- `btc_realized_vol_30d`: std of log returns over last 30d annualized
- `vol_percentile_180d`: where the current realized vol sits in the past 180 days
- `volatility`: `high` if percentile > 0.6, `low` if < 0.4, else carry previous label
- `regime_cell`: `f"{trend}-{volatility}"`

---

## CLI added

```text
ats process run                        # one-shot: compute features for all symbols at the most recent closed bar
ats process backfill --since 90d       # compute features for the past N days (idempotent)
ats process watch                      # [Tier 3] subscribe to candle-close events; compute as bars close
ats regime show [--history 30d]        # current regime + recent history
ats process validate                   # programmatic smoke test
```

### Skill surface

**None.** Feature computation is plumbing — neither a coding agent nor a human invokes it
directly. Tier 1 fires it inside `ats session run`; Tier 3 daemonizes `ats process watch`.

---

## Validation

### Smoke test

```bash
# (Phase 1 must already have ≥ 30d of candles)
uv run ats process backfill --since 30d --symbols BTCUSDT,ETHUSDT
uv run ats process run
uv run ats regime show
```

Expected:

```
features  BTCUSDT  15m  rows: ~2880   pr_atr ∈ [0, 1] all rows ✓
features  ETHUSDT  15m  rows: ~2880   pr_atr ∈ [0, 1] all rows ✓
regimes               rows: ~720     latest: bull-low  (slope +0.7%/d, vol pct 0.32)
```

### Acceptance criteria

- [ ] **Indicator parity (RSI)**: for BTCUSDT 15m on the 100 most recent bars, `features.rsi_14` matches a reference implementation (TradingView screenshot or `ta` library cross-check) within ±0.05
- [ ] **Indicator parity (ATR)**: same as above within ±1%
- [ ] **Normalization bound**: `SELECT count(*) FROM features WHERE pr_atr < 0 OR pr_atr > 1` returns 0
- [ ] **No look-ahead**: at any `open_time = T`, no feature column was computed using a candle with `open_time ≥ T`
- [ ] **Regime coverage**: every closed BTC 1h bar in the past 30d has a corresponding `regimes` row
- [ ] **Idempotent recompute**: `process backfill --since 30d` twice → 0 net new rows the second time
- [ ] **Latency (Tier 3 only)**: features for a closed 15m bar are materialized within 10 seconds of close (test by reading `computed_at - open_time - 15min`)
- [ ] **Tier parity**: running `ats process backfill --since 7d` (Tier 1 path) and the equivalent event-driven `ats process watch` runs over the same candles produce **byte-identical** feature rows (same `pr_*`, same composites). A test diffs two runs on a shared fixture.

### pytest

| File | Asserts |
|---|---|
| `tests/test_indicators.py` | RSI/ATR/MACD on a 200-bar synthetic series match golden values |
| `tests/test_percentile_rank.py` | uniform input → linear ranks; window respects `closed='left'` |
| `tests/test_composite.py` | edge inputs (rsi=30, macd=0) → expected boundary outputs |
| `tests/test_regime.py` | fixture BTC bars → expected regime cell |
| `tests/test_features_idempotency.py` | repeated upsert on the same key changes nothing material |

### `ats process validate`

1. Asserts `candles` has ≥ 30d for at least one symbol
2. Runs `process backfill --since 7d` against a temp schema or a marker
3. Asserts feature row count matches expected (≈ 7 × 96)
4. Asserts `pr_*` columns are all in `[0, 1]`
5. Exits 0 on success

---

## Risks / open questions

- **`pandas-ta` API drift** — pin to an exact version; if the API breaks, switch to `ta` (smaller, simpler) before fighting with pandas-ta
- **Look-ahead bias** — the single biggest correctness risk in this phase. Every rolling op uses `closed='left'`. Every test fixture verifies it.
- **Cold-start gap** — symbols newly listed have < 30d of history. For those, `pr_*` are NULL; downstream (Phase 3) must handle NULL as "exclude from screen"
- **Regime hysteresis** — naïve threshold flipping causes regime ping-pong. Use a small dead-zone (`bull` requires slope > +0.5%/d, `bear` requires < -0.5%/d; in between is `side`), and `volatility` carries its previous label inside the 0.4–0.6 percentile band.
- **Cost of computing all symbols × all timeframes** — at 50 symbols × 3 tf, full backfill of 90d should complete in < 5 minutes; if it's slower, batch by symbol with vectorized pandas-ta and avoid per-row Python loops
