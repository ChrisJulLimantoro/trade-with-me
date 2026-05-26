# Roadmap & Spec Overview

This directory holds the per-spec plans for the Agentic Trading System,
organized into **four milestones**. Each spec is self-contained: read it, do the
work, validate it, ship it. Specs are the source of truth for *what gets built*;
`architecture.md` is the source of truth for *why*; `README.md` is the live
**progress tracker**.

---

## Current progress (2026-05-26)

| Spec | Status | Blocking next? |
|---|---|---|
| **01 — Data Collection** | 🏗️ All code + 9 test files written. 23/26 tests passing. DB smoke test not yet run. | Yes — must close before spec 02 |
| 02–10 | 🔲 Not started | — |

**Next action:** run the spec 01 smoke test against a live DB, tick all acceptance criteria, then move to spec 02.

```bash
docker compose -f ops/docker-compose.yml up -d
uv run ats db migrate
uv run ats ingest backfill --since 7d --symbols BTCUSDT,ETHUSDT
uv run ats ingest xvenue-funding --symbols BTCUSDT,ETHUSDT
uv run ats ingest media-pull
uv run ats data status
uv run ats data validate
```

---

## Milestones

| Milestone | Specs | Purpose | Gate to exit |
|---|---|---|---|
| **M1 — Prove Edge** | 01–06 | Cheapest end-to-end *deterministic* signal over ~5 majors, exercised by a replay harness. | **Decision gate** (`06`) returns **go** |
| **M2 — Sharpen** | 07–08 | LLM ±0.20 layer, sentiment/narrative, universe → ~50 + diversity filter, learning loop. | Signal beats the M1 baseline; learnings accumulate |
| **M3 — Observe** | 09 | Read-only FastAPI + Next.js dashboard + MCP server + SKILL.md / CLI-parity contract. | A coding agent can drive the system; dashboard renders a live cycle |
| **M4 — Operate** | 10 | Promote to Tier 2 (cron) then Tier 3 (live daemons). | System runs unattended at the chosen tier |

**The single most important rule: do not start M2 until M1's decision gate is
green.** Everything in M2–M4 is wasted effort if the deterministic signal has no
edge. See `architecture.md` → "The hard truth".

---

## Spec order

We build in this order because each spec produces inputs the next consumes.

| # | Spec | Milestone | Theme |
|---|---|---|---|
| 1 | [01-data-collection.md](01-data-collection.md) | M1 | Binance REST + RSS ingestion over ~5 majors. |
| 2 | [02-data-processing.md](02-data-processing.md) | M1 | Indicators + percentile-rank normalization + regime detection. |
| 3 | [03-orchestration.md](03-orchestration.md) | M1 | Attention scoring, signal lifecycle, outcome reconciliation. |
| 4 | [04-deterministic-signal.md](04-deterministic-signal.md) | M1 | Eight deterministic agents (Structure / Momentum / Funding / Liquidity / PriceAction / CrossVenueFlow / Basis / CVD) + synthesizer → structured signal. |
| 5 | [05-replay-harness.md](05-replay-harness.md) | M1 | Run the pipeline over 90d of history → hit-rate report. |
| 6 | [06-decision-gate.md](06-decision-gate.md) | M1 | Numeric go/no-go: does the signal have edge? |
| 7 | [07-llm-layer.md](07-llm-layer.md) | M2 | LLM agents, ±0.20 hybrid deltas, vision, universe expansion + diversity. |
| 8 | [08-learning.md](08-learning.md) | M2 | Paper-trade outcomes → reflection; pgvector memory (sub-stage 8b). |
| 9 | [09-interface-ui-mcp.md](09-interface-ui-mcp.md) | M3 | Read-only FastAPI + dashboard + MCP server + skill/CLI-parity contract. |
| 10 | [10-live-operations.md](10-live-operations.md) | M4 | Tier 2 cron → Tier 3 daemons promotion playbook. |

---

## Validation philosophy

We do not advance to spec N+1 until spec N's acceptance criteria all pass. We do
not advance to **M2** until the **decision gate** (spec 06) is green.

Every spec ships with:

1. **Smoke test** — manual but fast (under 5 min) showing the spec works end-to-end
2. **Acceptance criteria** — boolean checks; either all pass or the spec isn't done
3. **pytest unit tests** — golden-fixture-based regression protection
4. **`ats <spec> validate`** — one command running the smoke test programmatically

When all four are green for a spec, **commit** with a `spec-N: done` tag, update
`README.md`, and move on.

---

## Cumulative state at each spec

Deps and infra marked **[M4]** are only required if you promote to Tier 3 (live).
M1–M3 run the same code without them.

| After spec | Python deps added | Infra added | Tables added | CLI / skills added |
|---|---|---|---|---|
| 0 (baseline) | `typer`, `pydantic`, `pydantic-settings`, `structlog`, `rich` | — | — | `ats --version`, `ats --help` |
| 1 | `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `python-binance`, `httpx`, `feedparser` | Postgres + TimescaleDB + pgvector (local / Neon free / Supabase free) | `candles` (+`taker_buy_vol`), `funding_rates`, `funding_rates_xvenue`, `basis`, `open_interest`, `mark_prices_1m` **[M4]**, `liquidations` **[M4]**, `media_items`, `heartbeats` | `ats db migrate`, `ats ingest backfill`, `ats ingest xvenue-funding`, `ats ingest media-pull`, `ats data status` |
| 2 | `pandas`, `numpy`, `pandas-ta` | — | `features`, `regimes` | `ats process run/backfill`, `ats regime show` |
| 3 | — | — | `top_picks`, `signals` | `ats screen run/top/replay`, `ats signal list/show/close`, `ats cycle run --now`, `ats session run` |
| 4 | — | — | `signals` extended (direction/entry/sl/tp/…), `agent_runs` | `ats analyze`, `ats agent <name> run`, `ats cycle run` |
| 5 | — | — | `paper_trades` (replay-populated) | `ats replay run`, `ats replay report` |
| 6 | — | — | — | `ats gate check` |
| 7 | `anthropic`, `ccxt`, `playwright` (optional, vision-gated), `tweepy` | — | `narratives`, `narrative_items`, `llm_costs` | `ats narratives`, LLM-path of `ats analyze` / `ats agent` |
| 8 | `pgvector` (client), `sentence-transformers` (8b) | — | `learnings` (8b), `reflections` | `ats learn post-mortem`, `ats reflect run`, `ats journal` |
| 9 | `fastapi`, `uvicorn`, `mcp` | — | — | `ats serve`, `ats serve --mcp`; skills authored; `ats skills validate`, `ats mcp validate`; UI in `ui/` |
| 10 | `redis[hiredis]` **[M4]**, `arq` **[M4]** | Redis container **[M4]** | — | `ats ingest start`, `ats process watch`, `ats screen watch`, `ats learn worker`, `ats serve --live` |

### Tier matrix per spec

| Spec | Tier 1 (session, $0 idle) | Tier 2 (scheduled) | Tier 3 (live daemons) |
|---|---|---|---|
| 1 — Data collection | REST backfill on session start (Binance + Bybit/OKX/Hyperliquid xvenue funding + premiumIndex) | Cron → REST backfill | WS streaming + freshness watchdog |
| 2 — Processing | Synchronous in-session compute | Same as Tier 1 | Event-driven on candle-close |
| 3 — Orchestration | `ats session run` reconciles + cycles in one shot | Cron → `ats session run` | arq worker on candle-close |
| 4 — Deterministic signal | Inside `session run`; eight agents called directly | Same as Tier 1 | Same code; queued per top-pick |
| 5 — Replay harness | One-shot `ats replay run` over history | n/a (a validation tool) | n/a |
| 6 — Decision gate | One-shot `ats gate check` | n/a | n/a |
| 7 — LLM layer | Inside `session run` | Same as Tier 1 | Queued per top-pick |
| 8 — Learning | Reconcile + post-mortem at session start | Same as Tier 1 | Mark-price poller + immediate post-mortem |
| 9 — Interface | `ats serve` on demand | Same as Tier 1 | `ats serve --live` daemonized |
| 10 — Live operations | n/a (this spec *is* the promotion) | cron wiring | the daemon set |

The code path is the **same** across tiers; only the invocation differs. Tiers 2
and 3 are an **M4** concern — ignore the `[M4]` rows until then.

### Skill & MCP inventory

The skill surface (`/cycle-now`, `/analyze-symbol`, `/agent-*`, `/post-mortem`,
`/weekly-reflection`) and the read-only MCP tool set are **authored in M3**
(`specs/09-interface-ui-mcp.md`). M1–M2 are **CLI-first** — every workflow is a
plain `ats <verb>` command. The skill wrappers come once the CLI surface is
stable; building both in lockstep doubles the surface area for no gain.

---

## Cross-spec conventions

### Time
All timestamps are UTC `timestamptz`. Bar boundaries use Binance's `open_time`
exactly. No tz juggling. Never compare wall-clock to `open_time` without aligning
to bar boundaries.

### Idempotency
Every insert is `INSERT ... ON CONFLICT DO UPDATE` keyed on the natural primary
key. Re-running any spec against the same input must produce the same output. Any
randomized sampling uses a fixed seed (`SEED` env var, default `42`).

### Observability
- **Logs:** `structlog` — JSON in prod, rich-rendered in dev, configured once in
  the CLI entry callback.
- **Cost log:** every LLM call writes a row to `llm_costs` (added in spec 07).
- **Freshness:** `ats data status` shows current data-class state and exits
  non-zero on staleness.

### Safety
- No write endpoints in the API; no write tools over MCP.
- No live execution anywhere.
- LLM never sees raw price text — only structured features (JSON only). Chart
  *images* (M2 vision) are a separate channel.
- LLM output is Pydantic-validated; parse failure → fall back to
  deterministic-only.
- LLM delta is hard-clamped to **±0.20** of the deterministic score.

### Errors
Boundaries between components (REST retries, DB transactions, WS reconnect) are
where error handling lives. Internal pure functions assume valid input and let
exceptions propagate. We never silently `except: pass`.

---

## Running the system

Two shapes — Tier 1 (default, $0 idle, M1–M3) and Tier 3 (live, opt-in, M4).
Tier 2 is just cron calling the Tier-1 command.

### Tier 1 — session mode (default, M1–M3)

```bash
# One-time
uv sync
uv run ats --version
uv run ats db migrate

# Run a cycle (zero idle cost between invocations)
uv run ats session run            # backfill → process → reconcile → cycle → analyze → journal → exit

# Or any individual surface
uv run ats analyze BTCUSDT
uv run ats replay run --since 90d         # M1 validation engine
uv run ats gate check                     # M1 decision gate
uv run ats reflect run --since 7d         # M2
uv run ats serve                          # M3: API + MCP; Ctrl-C to stop
```

### Tier 3 — live mode (M4, after the decision gate + M2 data + budget)

```bash
docker compose -f ops/docker-compose.yml up -d        # Postgres + Redis
uv run ats ingest start &                              # WS consumer
uv run ats process watch &                             # event-driven feature compute
uv run ats screen watch &                              # cycle worker on candle-close
uv run ats learn worker &                              # mark-price poller + immediate post-mortem
uv run ats serve --live                                # API + MCP server
(cd ui && npm install && npm run dev)
```

See `specs/10-live-operations.md` for the full promotion playbook.

---

## How to use these specs

Each spec is structured the same way:

1. **Goal** — one paragraph
2. **Milestone & scope** — which milestone, explicit in/out
3. **Dependencies on prior specs** — what must be true before you start
4. **New deps** — exact `uv add ...` commands and infra additions
5. **Data sources / data model** — what feeds the spec; SQL schemas
6. **Components** — concrete file paths and responsibilities
7. **CLI added** — exactly which new commands appear
8. **Validation** — smoke test, acceptance criteria, pytest plan
9. **Risks / open questions** — known unknowns

Treat acceptance criteria as a checklist. If you can't tick a box, you haven't
finished. If a criterion is wrong, *update the spec first*, then the code. After
each spec, update the status table in `README.md`.
