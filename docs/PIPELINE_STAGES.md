# Trading Pipeline — Stage-by-Stage I/O

The LLM-plan-driven POC moves data through six stages. It narrows at each step:
**full market envelope → strategic plan → boolean detection → confirm decision →
sized order → realized P&L.**

LLM calls happen only at **Stage 1** (`create_plan`, periodic) and **Stage 3**
(`confirm_setup`, and only when Stage 2 detects). Everything else is deterministic.

```
                  features / regimes / candles (specs 01 + 02)
                                   │
        ┌──────────────────────────▼──────────────────────────┐
        │ 1. create_plan        (LLM, periodic)                │  → Plan + Setups
        └──────────────────────────┬──────────────────────────┘
                                   │ active setups
        ┌──────────────────────────▼──────────────────────────┐
        │ 2. rule engine        (deterministic, every bar)     │  → SetupEval
        └──────────────────────────┬──────────────────────────┘
                       detected? ──┤ (else stop)
        ┌──────────────────────────▼──────────────────────────┐
        │ 3. confirm_setup      (LLM, only on detection)       │  → ConfirmOutput
        └──────────────────────────┬──────────────────────────┘
                  CONFIRM/REDUCE? ──┤ (else stop)
        ┌──────────────────────────▼──────────────────────────┐
        │ 4. risk manager       (deterministic)                │  → RiskDecision
        └──────────────────────────┬──────────────────────────┘
                       approved? ──┤ (else stop)
        ┌──────────────────────────▼──────────────────────────┐
        │ 5. executor           (paper only)                   │  → open PaperTrade
        └──────────────────────────┬──────────────────────────┘
                                   │ on later bars
        ┌──────────────────────────▼──────────────────────────┐
        │ 6. reconciliation     (deterministic)                │  → closed PaperTrade
        └───────────────────────────────────────────────────────┘

   invalidation runs alongside stages 2–6 (hard → kill plan, soft → pause, warning → log)
```

---

## Stage 1 — `create_plan` (LLM, periodic)

*Module:* `planning/create_plan.py` → `llm/client.py`

### Input — the envelope dict (`build_envelope`)
| Field | Source | Notes |
|---|---|---|
| `as_of` | feature `open_time` | the "now" timestamp (`<= as_of` in replay) |
| `symbol`, `timeframe` | request | |
| `features` | latest `features` row | ~32 indicator / normalized columns |
| `regime` | latest `regimes` row | cell, trend, vol |
| `recent_ohlcv` | last ~50 `candles` | |
| `portfolio` | open `paper_trades` + settings | equity_usd, open positions |
| `risk_limits` | settings | max_position_pct, min_rr, one-position-per-symbol |

### Output — `PlanOutput` (Pydantic, schema-validated)
| Field | Type | Notes |
|---|---|---|
| `market_bias` | `bullish \| bearish \| neutral` | |
| `rationale` | text | |
| `allowed_setups[]` | list of `SetupOutput` | each setup carries entry/exit + 3 rule lists |

Each **`SetupOutput`**: `direction (long\|short)`, `entry_zone [low, high]`,
`take_profit [..]`, `stop_loss`, `size_pct (0–1]`, `hard_rules`, `soft_rules`,
`invalidation_rules`.

### Persisted
1 `Plan` row + N `Setup` rows + 1 `LlmCall` audit row. The new plan **supersedes**
the prior active plan (status → `superseded`) — this is plan versioning.

---

## Stage 2 — Rule Engine (deterministic, every bar)

*Module:* `engine/rule_engine.py`

### Input
| Field | Source |
|---|---|
| `hard_rules`, `soft_rules` | the active setup |
| `features` | current bar, with `price`/`close`/`high`/`low` injected |
| `prev` | previous bar (needed only for `crosses_above` / `crosses_below`) |

Operand resolution: number → literal; `"price"` → current close; any other string →
`features.get(name)`. A `None`/NaN operand makes the rule **False** (conservative).

### Output — `SetupEval`
| Field | Meaning |
|---|---|
| `price_ok` | price inside `entry_zone` |
| `hard_ok` | all hard rules pass (failing names collected) |
| `soft_score` | weighted fraction of soft rules passing (0–1) |
| `detected` | `price_ok AND hard_ok AND soft_score >= soft_threshold` |

Only when `detected` is True does the pipeline call the LLM in Stage 3.

---

## Stage 3 — `confirm_setup` (LLM, only on detection)

*Module:* `engine/detector.py` → `llm/client.py`

### Input — confirm envelope
`{ now, symbol, setup{…}, rule_eval{price, hard_ok, soft_score, failed_hard},
features_now{…}, plan_bias }`

### Output — `ConfirmOutput`
| Field | Type | Notes |
|---|---|---|
| `action` | `CONFIRM \| REJECT \| WAIT \| REDUCE_SIZE` | only CONFIRM/REDUCE_SIZE proceed |
| `reason` | text | |
| `size_multiplier` | float (0–1] | clamped; applied in Stage 4 |

Persisted as another `LlmCall`. `REJECT`/`WAIT` → stop, no trade.

---

## Stage 4 — Risk Manager (deterministic)

*Module:* `risk/manager.py`

### Input
`direction`, `entry`, `stop_loss`, `take_profit`, setup `size_pct`,
confirm `size_multiplier`, current open positions.

### Output — `RiskDecision`
| Field | Meaning |
|---|---|
| `approved` | bool |
| `size_pct` | capped at `max_position_pct`, scaled by `size_multiplier` |
| `reward_risk` | computed R:R |
| `reasons[]` | audit trail |

Rejects if R:R `< min_rr`, or a position already exists for the symbol.

---

## Stage 5 — Executor (paper only)

*Module:* `execution/executor.py`

### Input
Approved `RiskDecision` + setup + entry price / time.

### Output
A new **open** `PaperTrade` row (`entry_price`, `size_pct`, `stop_loss`,
`take_profit`, `status=open`).

> **Hard safety rule:** the executor never places a live order. Paper only, permanently.

---

## Stage 6 — Reconciliation (deterministic)

*Module:* `execution/reconcile.py`

### Input
An open `PaperTrade` + subsequent candle(s) + invalidation result.

### Output — `ExitResult`
| Field | Meaning |
|---|---|
| `exit_price` | fill on exit |
| `exit_reason` | `sl \| tp \| expiry \| invalidation` |
| `pnl_pct`, `pnl_usd` | realized P&L |

**Same-bar priority:** invalidation → SL → TP → expiry. SL wins a same-bar SL+TP tie
(conservative). Closes the `PaperTrade` (`status=closed`).

---

## Cross-cutting — Invalidation

*Module:* `engine/invalidation.py` — runs alongside stages 2–6.

**Input:** setup `invalidation_rules` + current/prev features + `candle_closed`
(`on_close` rules fire only on a closed candle).

**Output:** highest triggered severity, or `None`.

| Severity | Effect |
|---|---|
| `hard` | kill the plan (status → invalidated), close its open trades, request replan |
| `soft` | pause new entries |
| `warning` | log only |

---

## How to Test (CLI)

All commands run via `uv run ats …`. The pipeline runs against a Postgres DB and
works end-to-end with **no API key** (LLM defaults to mock — see `llm_mock` in `config.py`),
so the full flow below is reproducible offline.

### 0. One-time setup — DB + data

```bash
uv run ats db migrate                              # apply Alembic migrations
uv run ats ingest backfill --since 120d            # candles + funding + OI + premium (Tier 1)
uv run ats process backfill --since 90d            # compute features (+ BTC regimes)
uv run ats process validate                        # smoke test: candles ≥30d, pr_* ∈ [0,1]
```

`process backfill` also computes regimes when `BTCUSDT` is in the universe — Stages 1
and 2 both need features/regimes to exist first.

### Per-stage inspection

| Stage | Command | What to look for |
|---|---|---|
| inputs | `uv run ats process show --symbol BTCUSDT --timeframe 15m` | coverage table + latest row + sparklines |
| inputs | `uv run ats regime show --history 30d` | current `regime_cell`, trend/vol history |
| 1 `create_plan` | `uv run ats plan create --symbol BTCUSDT --timeframe 15m` | a `Plan` + `Setups` table + `llm:` audit line |
| 1 | `uv run ats plan show --symbol BTCUSDT` | recent plans; new plan `active`, prior ones `superseded` |
| 1 | `uv run ats plan detail <plan_id>` | header, rationale, setups **with rules** (hard/soft/invalidation) |
| 1 | `uv run ats plan setups --plan-id <plan_id>` | just the setups for a plan |
| 2–6 (single bar) | `uv run ats engine tick --symbol BTCUSDT --timeframe 15m` | `detections / opened / closed / confirm_calls / invalidated` for the latest bar |
| cross-cutting | `uv run ats engine invalidate-check --symbol BTCUSDT` | worst invalidation severity (`none/warning/soft/hard`) on latest bar |
| 1–6 (full loop) | `uv run ats engine replay --symbol BTCUSDT --timeframe 15m --since 30d` | per-run `bars/plans/detections/opened/closed/invalidations` + cumulative trade summary |
| 5–6 outputs | `uv run ats trades show --status closed --symbol BTCUSDT` | individual paper trades with entry/exit/reason/pnl |
| 5–6 outputs | `uv run ats trades stats --since 90d --symbol BTCUSDT` | win rate, avg pnl, and a by-exit-reason breakdown |

### Full replay walkthrough

`engine replay` exercises every stage in time order — on each historical bar it refreshes
the plan when stale (Stage 1), runs the rule engine (Stage 2), and on a detection calls
confirm → risk → execute (Stages 3–5), reconciling open trades against later bars (Stage 6):

```bash
uv run ats engine replay --symbol BTCUSDT --timeframe 15m --since 30d
uv run ats trades stats --since 30d --symbol BTCUSDT     # then inspect the outcome
```

> **Note — replay numbers accumulate.** `paper_trades` is never reset between runs, so the
> trade summary printed at the end of `replay` (and `trades stats`) counts *all* trades for
> the symbol across every prior run, not just the current one. The `opened/closed` counts in
> the replay report line *are* per-run. For a clean per-run read, truncate the table first,
> e.g. `psql "$DATABASE_URL" -c "TRUNCATE paper_trades;"`. Also note `replay` generates a
> rolling sequence of plans (one every `plan_refresh_bars`, ~16 bars), not the single plan
> shown by `plan show`.

### Live loop (optional)

```bash
uv run ats engine run --symbol BTCUSDT --timeframe 15m --once --refresh   # one tick, refresh data first
uv run ats engine run --symbol BTCUSDT --timeframe 15m --interval 60      # poll loop (Ctrl-C to stop)
```

> Spec 01 is REST-only, so the live loop is **not** tick-precise — `--refresh` backfills
> candles + recomputes features before each tick.
