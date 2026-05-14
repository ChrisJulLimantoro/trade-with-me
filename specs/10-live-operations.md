# Spec 10 — Live Operations · Milestone M4

> The "final picture." Everything in M1–M3 runs at **Tier 1** — zero idle cost,
> manual or on-demand invocation. M4 is the opt-in promotion path: first to
> **Tier 2** (cron calling the same command), then to **Tier 3** (long-running
> daemons). The Python code does not change — only the invocation does. This
> spec is the playbook for that promotion, and it consolidates every `[M4]` /
> `[Tier 3]` aside scattered across specs 01–09 into one place.
>
> **Promote only when all three are true:** the decision gate (spec 06) was GO,
> spec 08 has produced a meaningful body of closed forward trades, and there is
> budget for always-on infra. Until then, this spec is reference, not work.

---

## Goal

1. **Tier 2:** run `ats session run` unattended on a schedule (cron) — DB is the
   only persistent infra.
2. **Tier 3:** promote the same coroutines into long-running daemons —
   WebSocket ingestion, event-driven feature compute, a cycle worker, a
   mark-price outcome poller, and a daemonized API/MCP server. Adds Redis.

The acceptance bar for M4 is operational, not analytical: the system runs at the
chosen tier without manual intervention and without dropping data.

---

## Milestone & scope

**Milestone:** M4 — Operate.

**In:**
- **Tier 2:** cron wiring for `ats session run`; lock-file guard against
  overlapping runs; failure alerting
- **Tier 3:** the daemon set —
  - `ats ingest start` — Binance WS consumer (klines, markPrice, forceOrder),
    funding/OI pollers, X+RSS pollers, freshness watchdog
  - `ats process watch` — event-driven feature compute on candle-close
  - `ats screen watch` — arq cycle worker on candle-close events
  - `ats learn worker` — mark-price poller + immediate post-mortem
  - `ats serve --live` — daemonized API + MCP with Redis-backed SSE fanout
- Redis + arq, `ops/docker-compose.yml` extended
- The `mark_prices_1m` and `liquidations` tables become live-fed (created in
  spec 01, dormant until now)
- Freshness watchdog: per-data-class heartbeats; `ats data status` reflects live
  state and exits non-zero on staleness

**Out:**
- Auto-execution (always out)
- Any change to signal logic, agents, synthesizer, or learning — M4 is purely an
  invocation/runtime change

---

## Dependencies on prior specs

- **Spec 06: GO**, **spec 08:** a body of closed forward paper trades, and a
  decision to spend on infra. All three. See the promotion checklist below.
- Every spec 01–09: M4 daemonizes their code paths; it does not add new ones.

---

## New deps to add

```bash
uv add 'redis[hiredis]' arq
```

Extend `ops/docker-compose.yml`:

```yaml
  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    ports: ["127.0.0.1:6379:6379"]
    volumes: ["redis_data:/data"]
    healthcheck: ...
```

At Tier 3, also consider promoting Postgres from a free-tier host to managed
Timescale — see `architecture.md` → "Stack".

---

## Tier 2 — Scheduled

The minimal promotion. No daemons, no Redis. A scheduler (cron, systemd timer,
or a scheduled remote agent) calls the **exact Tier-1 command**:

```cron
*/15 * * * *  cd /path/to/ats && uv run ats session run >> logs/session.log 2>&1
```

Requirements:
- **Overlap guard:** `ats session run` takes a lock file; a second invocation
  while one is running exits cleanly with a logged notice.
- **Failure alerting:** non-zero exit pings the operator (the mechanism is the
  operator's choice — email, webhook, etc.; the CLI just needs to exit non-zero
  with a structured error).
- **Idempotency** (already guaranteed by the cross-spec conventions) means a
  missed or doubled cron tick is harmless.

That is the whole of Tier 2. Most operators may never need Tier 3.

---

## Tier 3 — Live

The same coroutines, promoted to daemons. Startup:

```bash
docker compose -f ops/docker-compose.yml up -d        # Postgres + Redis
uv run ats db migrate
uv run ats ingest backfill --since 7d                 # warm start
uv run ats ingest start &                              # WS + continuous pollers + watchdog
uv run ats process watch &                             # event-driven feature compute
uv run ats screen watch &                              # arq cycle worker
uv run ats learn worker &                              # mark-price poller + post-mortem
uv run ats serve --live                                # API + MCP + Redis SSE fanout
(cd ui && npm run dev)
```

### What each daemon adds over its Tier-1 form

| Daemon | Tier-1 equivalent | What Tier-3 adds |
|---|---|---|
| `ats ingest start` | `ats ingest backfill` (one-shot REST) | WS streaming (klines, `markPrice@1s` → 1m downsample, `forceOrder` liquidations), continuous funding/OI pollers, 6-hourly universe refresh, freshness watchdog |
| `ats process watch` | synchronous compute in `session run` | subscribes to candle-close events; computes incrementally; same byte-identical feature rows |
| `ats screen watch` | `run_cycle()` in `session run` | arq worker triggered on candle-close; enqueues per-symbol analysis jobs |
| `ats learn worker` | spec 03 session-start candle-replay reconciliation | polls `mark_prices_1m` every 10s for open trades; closes tick-precise; runs the post-mortem immediately on close |
| `ats serve --live` | `ats serve` (on-demand) | daemonized; SSE fanout via Redis pub/sub channel `ats:events` |

### Freshness watchdog

Per-data-class heartbeats with staleness budgets (kline_15m: 16m, kline_1h: 61m,
kline_4h: 4h1m, funding: 8h30m, open_int: 6m, mark_price: 90s, media_rss: 35m,
media_x: 65m). `ats data status` prints a rich table and exits non-zero when any
class is `stale`, `2×` budget = `missing`.

---

## Components

| Path | Responsibility |
|---|---|
| `src/ats/ingestion/binance_ws.py` | WS client; async reconnect with expo backoff; dispatches to writers |
| `src/ats/ingestion/freshness.py` | heartbeat writers + `check_freshness()` |
| `src/ats/processing/scheduler.py` | candle-close event subscriber → triggers compute |
| `src/ats/orchestration/scheduler.py` | arq worker; subscribes to candle-close |
| `src/ats/cache/redis.py` | `set_top_picks()`, `get_latest_top()`, pub/sub helpers |
| `src/ats/journal/outcome_tracker.py` | the Tier-3 mark-price poller path (the Tier-1 path already exists from spec 08) |
| `src/ats/api/events.py` | Redis-backed SSE event publisher (the Tier-1 no-op already exists from spec 09) |
| `ops/docker-compose.yml` | Postgres + Redis |
| `ops/cron.example` | the Tier-2 crontab template + lock-file wrapper |

Note: most of these files already exist from earlier specs in their Tier-1 form.
M4 fills in the daemon code paths behind the same module names — it does not
introduce a parallel implementation.

---

## CLI added

```text
ats ingest start                     # WS + continuous pollers + freshness watchdog until SIGINT
ats process watch                    # subscribe to candle-close events; compute as bars close
ats screen watch                     # arq cycle worker on candle-close
ats learn worker                     # mark-price outcome poller + immediate post-mortem
ats serve --live                     # daemonized API + MCP with Redis SSE fanout
ats ops validate                     # programmatic smoke test (30-min live run)
```

---

## Promotion checklist

Do **not** start M4 work until every box is ticked:

- [ ] `data/gate_<date>.md` records a **GO** verdict (spec 06)
- [ ] Spec 08 (8a) has run forward long enough to produce a body of closed
      `source='session'` trades whose hit rate is consistent with the gate's
      replay estimate (forward ≈ replay, within noise)
- [ ] There is an explicit budget decision for always-on infra (Redis host,
      possibly managed Postgres, compute)
- [ ] You actually need it — re-read `architecture.md` → "What you lose by not
      running 24/7". If Tier 2 cron is sufficient, stop there.

If forward results diverge sharply from the replay estimate, **that is a
signal-quality problem** — go back to M1/M2, do not paper over it with infra.

---

## Validation

### Tier 2 smoke test

```bash
# add the cron line, then wait two ticks
tail -f logs/session.log
uv run ats journal --status closed     # rows accumulating from unattended runs
```

### Tier 3 smoke test — 30-min run

```bash
docker compose -f ops/docker-compose.yml up -d
uv run ats db migrate
uv run ats ingest backfill --since 7d
uv run ats ingest start &
uv run ats process watch &
sleep 1900
uv run ats data status
```

Expected: every data-class `ok` with realistic ages (`kline_15m` < 2m,
`mark_price` < 90s, `media_rss` < 35m).

### Acceptance criteria

- [ ] **Tier 2 overlap guard:** a second `ats session run` started while one is running exits cleanly with a logged notice; no doubled rows
- [ ] **Tier 2 failure alert:** a forced failure produces a non-zero exit and the configured alert fires
- [ ] **Live ingest:** ≥ 1 new `kline_15m` row inserted during a 30-min Tier-3 run
- [ ] **Reconnect:** `pkill -STOP` the ingester for 60s then resume → reconnects within 30s, no dropped bars
- [ ] **Mark-price downsampling:** 60 `markPrice@1s` samples → 1 `mark_prices_1m` row with correct OHLC
- [ ] **No duplicate liquidations:** the same `forceOrder` inserted twice → one row
- [ ] **Freshness exit codes:** `ats data status` exits 0 when all green, 1 when any class is `missing`
- [ ] **Tick-precise close parity:** a trade closed by the Tier-3 mark-price poller and the same trade closed by Tier-1 candle reconciliation produce the same `exit_reason` (exit prices may differ slightly; the reason must not)
- [ ] **Tier parity on features:** event-driven `ats process watch` produces byte-identical feature rows to the Tier-1 synchronous path over the same candles
- [ ] **SSE fanout:** a `cycle_close` published by the cycle worker reaches a connected dashboard client within 1s

### pytest

| File | Asserts |
|---|---|
| `tests/test_binance_ws_parsing.py` | fixture WS payload → expected row |
| `tests/test_ws_reconnect.py` | simulated drop → reconnect, no lost bars |
| `tests/test_markprice_downsample.py` | 60 1s samples → 1 1m row, correct OHLC |
| `tests/test_freshness.py` | seeded heartbeat ages → correct status |
| `tests/test_session_lock.py` | overlapping `session run` → second exits cleanly |
| `tests/test_tier_parity_features.py` | watch path == backfill path, byte-identical |
| `tests/test_outcome_tracker_tick.py` | tick stream → same `exit_reason` as candle reconciliation |

### `ats ops validate`

1. Brings up Postgres + Redis
2. Spawns the ingester in a subprocess for 90s
3. Asserts ≥ 1 row added to a recent table; heartbeats `ok`
4. Publishes a fake SSE event; asserts an HTTP client receives it
5. Cleans up; exits 0

---

## Risks / open questions

- **Infra creep.** Tier 3 is the only part of this whole project with non-zero
  idle cost. Promote deliberately. Tier 2 cron covers most unattended-ops needs
  at DB-only cost.
- **`forceOrder` floods.** At high-volatility moments liquidation events flood;
  the writer must batch (≥ 50ms windows) before inserting.
- **WS reconnect correctness.** The single biggest Tier-3 correctness risk. The
  reconnect path must backfill the gap via REST, not just resume — covered by
  `test_ws_reconnect.py`.
- **Clock skew.** Binance `open_time` is authoritative; the local clock is for
  nothing. Never compare `now()` to `open_time` without aligning to bar
  boundaries.
- **Forward ≠ replay.** If live results diverge from the spec-05 replay estimate,
  it is a signal problem, not an ops problem. Resist the urge to fix it with more
  infra.
