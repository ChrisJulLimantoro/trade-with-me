# Spec 01 — Data Collection · Milestone M1

> The heaviest spec. Without complete, validated data, nothing else
> in the system has meaning. Spend the time here.
>
> **M1 universe:** ~5 hand-picked majors — `BTCUSDT, ETHUSDT, SOLUSDT` + 2 more.
> Five is enough for the BTC regime proxy to be meaningful and to see relative
> behavior; small enough that data volume, cost, and maintenance stay trivial.
> The universe expands to ~50 in M2 (spec 07), which also turns on the 6-hourly
> top-50 universe resolver and X/Twitter ingestion.
>
> **Tiered:** Tier 1 ingests via REST on session start (zero idle cost). Tier 3
> (an **M4** concern — `specs/10-live-operations.md`) promotes to continuous WS
> streaming once the system has earned trust. The Python code is the same; only
> the invocation differs — so the WS tables below are created now but stay
> dormant until M4.

---

## Goal

Collect every input the system will ever need:

- **Market data** from Binance USDT-margined perpetual futures
- **Media data** (news + curated X/Twitter accounts)

Persist time-series into Postgres / TimescaleDB tables. Expose ingestion health.

### Tier 1 behavior (default, $0 idle — M1)

- `ats ingest backfill --since 120d` runs once at session start. Binance REST
  only. Idempotent. 120 days because the replay harness (spec 05) needs a 90d
  window plus 30d of percentile-rank warm-up.
- RSS poller does a one-shot pull on session start (cheap, idempotent).
- **X/Twitter ingestion is deferred to M2** (spec 07). M1 needs no X token at
  all; RSS alone is enough. The `x_poller` module is stubbed and reports
  `media_x: disabled`.
- No WebSocket. No freshness watchdog daemon. `ats data status` is a one-shot
  CLI command.
- Typical 5-symbol 120d backfill at 1h timeframe: a few seconds.

### Tier 3 behavior (live, opt-in — M4)

- `ats ingest start` runs the WS consumers, mark-price downsampler, liquidation
  writer, funding/OI pollers, X+RSS pollers, and the freshness watchdog
  continuously. Fully specified in `specs/10-live-operations.md`.
- Heartbeats fire per data-class; `ats data status` reflects live state.

---

## Scope

**Milestone:** M1 — Prove Edge.

**In (M1, Tier 1, default):**
- Fixed ~5-symbol universe (`BTCUSDT, ETHUSDT, SOLUSDT` + 2) hard-coded in
  `seeds/universe_m1.yaml` — no dynamic resolver yet
- Binance Futures REST: kline backfill (15m, 1h, 4h), funding rates, open interest
- Media (single-pass at session start): RSS via feedparser
- Persistence: TimescaleDB hypertables for time-series; regular tables for media items and heartbeats
- `ats data status` as a one-shot CLI inspection command
- Historical backfill: REST-based kline backfill for cold-start (120d)

**In (M2 — spec 07):**
- Dynamic top-50 universe resolver via `exchangeInfo` + 24h ticker, refreshed every 6h
- X/Twitter ingestion via tweepy

**In (M4 — `specs/10-live-operations.md`):**
- Binance Futures WS: kline (15m, 1h, 4h), markPrice (1s → 1m), forceOrder (liquidations)
- Continuous X + RSS polling
- Freshness watchdog daemon emitting heartbeats

**Out:**
- No indicator computation (spec 02)
- No scoring, ranking, or analysis (spec 03+)
- No LLM calls (spec 07)
- No orderbook depth (deferred — not required)

---

## Dependencies on prior phases

None — this is the foundation.

---

## New deps to add

```bash
uv add sqlalchemy[asyncio] asyncpg alembic
uv add python-binance httpx
uv add tweepy feedparser
```

Also create `ops/docker-compose.yml` running:

- `timescale/timescaledb-ha:pg16` (includes TimescaleDB + pgvector)

Redis is **not** required in Phase 1 and is intentionally deferred to Phase 3.

---

## Data sources

### Binance WebSocket
- Endpoint: `wss://fstream.binance.com/stream`
- Per-symbol subscriptions: `<sym>@kline_15m`, `<sym>@kline_1h`, `<sym>@kline_4h`, `<sym>@markPrice@1s`, `<sym>@forceOrder`
- Symbol universe: top-50 USDT-perp by rolling 24h quote volume, refreshed every 6h from REST `/fapi/v1/exchangeInfo` + `/fapi/v1/ticker/24hr`
- Persist klines on `kline closed = true` only (we never store open candles)
- Downsample `markPrice@1s` to 1-minute averages before storage

### Binance REST
- `GET /fapi/v1/fundingRate?symbol=...&limit=1000` — pull latest funding history per symbol every 30 min
- `GET /fapi/v1/openInterest?symbol=...` — poll every 5 min per symbol
- `GET /fapi/v1/klines?symbol=...&interval=...&startTime=...` — historical backfill on demand
- Rate-limit budget: stay under 1500 weight/min (Binance allows 2400) so we leave headroom

### Media — X/Twitter
- Curated account list in `seeds/x_accounts.yaml` (~50 accounts: top crypto analysts, exchanges, project accounts)
- Use tweepy `Client.get_users_tweets`, polling every 60 min
- Skip cleanly when `X_BEARER_TOKEN` is not set — heartbeat reports `disabled`

### Media — RSS
- Source list in `seeds/rss_feeds.yaml` (CoinDesk, The Block, Decrypt, CryptoPanic free, …)
- `feedparser` polled every 30 min

---

## Data model

### candles (hypertable)
```sql
CREATE TABLE candles (
  symbol         TEXT NOT NULL,
  timeframe      TEXT NOT NULL,          -- '15m' | '1h' | '4h'
  open_time      TIMESTAMPTZ NOT NULL,
  open           NUMERIC NOT NULL,
  high           NUMERIC NOT NULL,
  low            NUMERIC NOT NULL,
  close          NUMERIC NOT NULL,
  volume         NUMERIC NOT NULL,
  quote_volume   NUMERIC NOT NULL,
  trades         INT,
  ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (symbol, timeframe, open_time)
);
SELECT create_hypertable('candles', 'open_time', if_not_exists => TRUE);
```

### funding_rates
```sql
CREATE TABLE funding_rates (
  symbol         TEXT NOT NULL,
  funding_time   TIMESTAMPTZ NOT NULL,
  rate           NUMERIC NOT NULL,
  ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (symbol, funding_time)
);
```

### open_interest (hypertable)
```sql
CREATE TABLE open_interest (
  symbol         TEXT NOT NULL,
  ts             TIMESTAMPTZ NOT NULL,
  oi             NUMERIC NOT NULL,         -- contracts
  oi_value       NUMERIC,                  -- notional in quote
  ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (symbol, ts)
);
SELECT create_hypertable('open_interest', 'ts', if_not_exists => TRUE);
```

### liquidations
```sql
CREATE TABLE liquidations (
  id             BIGSERIAL PRIMARY KEY,
  symbol         TEXT NOT NULL,
  ts             TIMESTAMPTZ NOT NULL,
  side           TEXT NOT NULL,            -- 'BUY' (short liq) | 'SELL' (long liq)
  price          NUMERIC NOT NULL,
  qty            NUMERIC NOT NULL,
  notional       NUMERIC NOT NULL
);
CREATE INDEX idx_liq_symbol_ts ON liquidations (symbol, ts DESC);
```

### mark_prices_1m (hypertable)
```sql
CREATE TABLE mark_prices_1m (
  symbol         TEXT NOT NULL,
  ts             TIMESTAMPTZ NOT NULL,     -- minute bucket
  mark_open      NUMERIC NOT NULL,
  mark_close     NUMERIC NOT NULL,
  mark_high      NUMERIC NOT NULL,
  mark_low       NUMERIC NOT NULL,
  samples        INT NOT NULL,
  PRIMARY KEY (symbol, ts)
);
SELECT create_hypertable('mark_prices_1m', 'ts', if_not_exists => TRUE);
```

### media_items
```sql
CREATE TABLE media_items (
  id             UUID PRIMARY KEY,
  source         TEXT NOT NULL,            -- 'x' | 'rss'
  source_id      TEXT NOT NULL,            -- tweet id or feed entry id
  account        TEXT NOT NULL,
  published_at   TIMESTAMPTZ NOT NULL,
  url            TEXT,
  title          TEXT,
  body           TEXT NOT NULL,
  raw            JSONB,
  ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source, source_id)
);
CREATE INDEX idx_media_published ON media_items (published_at DESC);
```

### heartbeats
```sql
CREATE TABLE heartbeats (
  data_class     TEXT PRIMARY KEY,         -- 'kline_15m' | 'funding' | 'media_x' | ...
  last_seen_at   TIMESTAMPTZ NOT NULL,
  status         TEXT NOT NULL,            -- 'ok' | 'stale' | 'missing' | 'disabled'
  detail         TEXT,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## Components

| Path | Responsibility |
|---|---|
| `src/ats/config.py` | `Settings` (pydantic-settings) reading `.env` |
| `src/ats/logging.py` | `configure_logging()` — structlog setup |
| `src/ats/db/session.py` | async engine + `async_sessionmaker` |
| `src/ats/db/models.py` | SQLAlchemy declarative models for the tables above |
| `alembic/` | migrations; first migration creates all Phase 1 tables + hypertables |
| `src/ats/ingestion/universe.py` | top-N USDT-perp symbol fetcher (REST); caches in memory |
| `src/ats/ingestion/binance_ws.py` | WS client; async reconnect with expo backoff; dispatches to writers |
| `src/ats/ingestion/binance_rest.py` | funding + OI poller; exchange-info refresher |
| `src/ats/ingestion/x_poller.py` | tweepy poll; graceful when token absent |
| `src/ats/ingestion/rss_poller.py` | feedparser poll |
| `src/ats/ingestion/freshness.py` | heartbeat write helpers + `check_freshness()` |
| `src/ats/ingestion/backfill.py` | historical kline backfill via REST |
| `src/ats/cli/db.py` | `ats db ...` commands |
| `src/ats/cli/ingest.py` | `ats ingest ...` commands |
| `src/ats/cli/data.py` | `ats data status` command |
| `seeds/x_accounts.yaml` | curated X handles |
| `seeds/rss_feeds.yaml` | curated RSS feed URLs |
| `ops/docker-compose.yml` | Postgres + init.sql |
| `ops/init.sql` | `CREATE EXTENSION timescaledb; CREATE EXTENSION vector;` |

---

## Freshness budgets

| Data class | Max staleness | Status when exceeded |
|---|---|---|
| `kline_15m` | 16 min | `stale` |
| `kline_1h` | 61 min | `stale` |
| `kline_4h` | 4h 1m | `stale` |
| `funding` | 8h 30m | `stale` |
| `open_int` | 6 min | `stale` |
| `mark_price` | 90 sec | `stale` |
| `media_x` | 65 min | `stale` (or `disabled` if no token) |
| `media_rss` | 35 min | `stale` |

`ats data status` prints a rich table; exits non-zero if any class is **2×** its
budget (= `missing`).

---

## CLI added

```text
ats db migrate                       # alembic upgrade head
ats db downgrade --to <rev>          # rare; alembic downgrade
ats ingest backfill --since 7d       # REST kline + funding + OI backfill (Tier 1: run on session start; defaults to 7d)
ats ingest media-pull                # one-shot RSS + X(optional) pull (Tier 1: run on session start)
ats ingest start                     # [Tier 3] WS + continuous pollers + freshness watchdog until SIGINT
ats data status                      # heartbeats + row counts (one-shot)
ats data validate                    # programmatic smoke test (see below)
```

### Skill surface

This phase exposes **no direct skill** of its own — it's pure ingestion plumbing. Both
tiers' ingestion paths are reached transitively: Tier 1's backfill runs at the start of
`ats session run` (skill twin: `/cycle-now`); Tier 3's daemon is started by hand or by
the operator's process supervisor.

---

## Validation

### Tier 1 smoke test — under 2 minutes

```bash
# 1. Postgres up (local, Neon, or Supabase free tier)
uv run ats db migrate
# 2. One-shot REST backfill — proves the entire Tier 1 ingestion path
uv run ats ingest backfill --since 7d --symbols BTCUSDT,ETHUSDT
# 3. One-shot media pull (RSS always, X if token set)
uv run ats ingest media-pull
# 4. Inspect
uv run ats data status
```

Expected `data status` output (Tier 1):

```
data_class      status   last_seen           rows_total
kline_15m       ok       <session ago        ~672 (7d × 96)
kline_1h        ok       <session ago        ~168
kline_4h        ok       <session ago        ~42
funding         ok       <session ago        ~21
open_int        ok       <session ago        ~21    (REST snapshots at session)
mark_price      n/a      —                   0       (Tier 3 only)
media_rss       ok       <session ago        ≥10
media_x         ok|disabled                  varies
```

### Tier 3 smoke test — 30 min run (only after promoting)

```bash
docker compose -f ops/docker-compose.yml up -d        # Postgres + Redis
uv run ats db migrate
uv run ats ingest backfill --since 7d --symbols BTCUSDT,ETHUSDT
uv run ats ingest start &                              # WS + continuous pollers
sleep 1900
uv run ats data status
```

Expected: every data-class in `ok` state with realistic ages (e.g., `kline_15m` <2m,
`mark_price` <90s, `media_rss` <35m).

### Acceptance criteria — Tier 1

- [ ] **Backfill complete**: `SELECT COUNT(*) FROM candles WHERE symbol='BTCUSDT' AND timeframe='15m' AND open_time > now() - INTERVAL '7 days'` returns ≥ 660 (allow a small gap budget)
- [ ] **Backfill idempotency**: re-running the backfill produces zero net new rows
- [ ] **Media pull idempotency**: re-running `ats ingest media-pull` against the same RSS feeds inserts only items not previously seen (UNIQUE constraint upheld)
- [ ] **No daemons**: after `ats ingest backfill` exits, `ps aux | grep ats` shows nothing
- [ ] **One-shot status**: `ats data status` runs without a background ingester and reports row counts from Postgres
- [ ] **X graceful skip**: with `X_BEARER_TOKEN` unset, `ats ingest media-pull` completes successfully and reports `media_x: disabled` in the next `ats data status`

### Acceptance criteria — Tier 3 (only required when promoting)

- [ ] **Live ingest**: at least one new `kline_15m` row inserted during a 30-min run
- [ ] **Reconnect**: `pkill -STOP` the ingester for 60s and resume → ingester reconnects within 30s and continues without dropping pending bars
- [ ] **Freshness exit codes**: `ats data status` exits `0` when all green, `1` when any class is `missing` (2× budget)
- [ ] **No duplicate liquidations**: insert the same forceOrder twice → only one row exists (`(symbol, ts, side, price, qty)` near-unique within 1s)
- [ ] **Mark-price downsampling**: 60 1s samples → 1 1m row with correct OHLC

### pytest

| File | Asserts |
|---|---|
| `tests/test_binance_ws_parsing.py` | given a fixture WS payload, the dispatch produces the expected row |
| `tests/test_freshness.py` | heartbeats seeded with various ages → correct status calculation |
| `tests/test_universe.py` | top-N filter excludes delisted symbols and respects N |
| `tests/test_backfill_idempotency.py` | run backfill twice on the same window → zero net inserts the second time |
| `tests/test_markprice_downsample.py` | 60 1s samples → 1 1m row with correct OHLC |

### `ats data validate`

A one-shot command that:
1. Connects to Postgres; fails if migration hasn't run
2. Spawns ingester in a subprocess for 90s
3. Asserts at least one row added to a recent table
4. Asserts heartbeats all `ok` or `disabled`
5. Cleans up subprocess
6. Exits 0 on success, prints a structured report on failure

---

## Risks / open questions

- **X API cost ($100/mo Basic)** — the pipeline must work end-to-end with `media_x = disabled` so a user can defer the X subscription. RSS alone is enough for Phase 1 validation.
- **TimescaleDB image on Apple Silicon** — verify `timescale/timescaledb-ha:pg16` pulls cleanly on aarch64; fall back to building locally if not.
- **`forceOrder` rate** — at high-vol moments this can be a flood. The writer must batch (≥ 50ms windows) before inserting.
- **Universe drift** — top-50 changes daily; we re-resolve every 6h. Symbols that fall out of the top-50 are NOT pruned from `candles` (keep the history).
- **Clock skew** — Binance ts is authoritative; local clock is for nothing. We never compare `now()` to `open_time` without aligning to bar boundaries.
- **`media_items.body` size** — RSS articles can be long. Truncate `body` to 10 KB on insert; full content is recoverable via the URL.
