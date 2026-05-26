# Agentic Trading System (ATS)

A signal generator for crypto perpetual futures. Screens the Binance Futures
universe, runs a small swarm of narrow analyzers on the top picks, emits
**paper-traded** structured signals, and learns from outcomes. **It does not
execute trades.**

This README is the **live progress tracker**. For *why* the system is built this
way, read [`architecture.md`](architecture.md) — especially "The hard truth". For
*what* gets built, read the [`specs/`](specs/00-roadmap.md).

---

## You are here

> **Current milestone:** M1 — Prove Edge
> **Current spec:** [01 — Data Collection](specs/01-data-collection.md) — all code + tests written; **run DB smoke test to close it out**
> **Decision gate (spec 06):** not yet evaluated

*Update the three lines above and the status tables below as work progresses.*

---

## Milestone progress

| Milestone | Specs | Status | Gate to exit |
|---|---|---|---|
| **M1 — Prove Edge** | 01–06 | 🏗️ 1/6 in progress | `ats gate check` returns **GO** (spec 06) |
| **M2 — Sharpen** | 07–08 | 🔲 Not started | M2 replay beats the M1 baseline; learnings accumulate |
| **M3 — Observe** | 09 | 🔲 Not started | A coding agent can drive the system; dashboard renders a live cycle |
| **M4 — Operate** | 10 | 🔲 Not started | System runs unattended at the chosen tier |

**The one rule:** do not start M2 until M1's decision gate is green.

---

## Spec status

Legend: 🔲 Not started · 🏗️ In progress · ✅ Done · ⛔ Blocked

| Spec | Milestone | Status | Gate (all acceptance criteria pass) |
|---|---|---|---|
| [01 — Data Collection](specs/01-data-collection.md) | M1 | 🏗️ code complete, tests ✅, awaiting DB smoke test | `ats data validate` green; 120d candles for ~5 majors |
| [02 — Data Processing](specs/02-data-processing.md) | M1 | 🔲 | `ats process validate` green; `pr_*` ∈ [0,1]; no look-ahead |
| [03 — Orchestration](specs/03-orchestration.md) | M1 | 🔲 | `ats screen validate` green; signal state machine + reconciliation correct |
| [04 — Deterministic Signal](specs/04-deterministic-signal.md) | M1 | 🔲 | `ats cycle validate` green; eight agents + synthesizer; no LLM dependency |
| [05 — Replay Harness](specs/05-replay-harness.md) | M1 | 🔲 | `ats replay validate` green; no look-ahead; deterministic; baselines present |
| [06 — Decision Gate](specs/06-decision-gate.md) | M1 | 🔲 | `ats gate check` runs; verdict recorded in `data/gate_<date>.md` |
| [07 — LLM Layer](specs/07-llm-layer.md) | M2 | 🔲 | `ats llm validate` green; ±0.20 clamp holds; M2 replay ≥ M1 baseline |
| [08 — Learning](specs/08-learning.md) | M2 | 🔲 | `ats reflect validate` green (8a); 8b only after ≥40 closed session trades |
| [09 — Interface: UI & MCP](specs/09-interface-ui-mcp.md) | M3 | 🔲 | `ats serve/skills/mcp validate` green; CLI-parity + prompt-parity pass |
| [10 — Live Operations](specs/10-live-operations.md) | M4 | 🔲 | `ats ops validate` green; promotion checklist all ticked |

**Decision gate verdict:** *not yet evaluated* — see `data/gate_<date>.md` once spec 06 runs.

---

## Spec 01 build log

Everything below was built and committed on the `f/spec-0` branch. No acceptance criteria have been run against a live database yet — that's the next step.

### What exists

| Area | Files | Notes |
|---|---|---|
| Config / logging | `src/ats/config.py`, `src/ats/logging.py` | pydantic-settings + structlog |
| DB layer | `src/ats/db/models.py`, `src/ats/db/session.py` | all 9 models, async engine |
| Migration | `alembic/versions/0001_initial.py` | creates all Phase 1 tables + hypertables |
| Ingestion | `src/ats/ingestion/backfill.py` | Binance REST kline + `taker_buy_vol` backfill |
| | `src/ats/ingestion/binance_rest.py` | funding, OI, premiumIndex pollers |
| | `src/ats/ingestion/xvenue_funding.py` | Bybit + OKX + Hyperliquid funding REST |
| | `src/ats/ingestion/rss_poller.py` | feedparser RSS pull |
| | `src/ats/ingestion/x_poller.py` | tweepy stub (graceful skip if no token) |
| | `src/ats/ingestion/freshness.py` | heartbeat write helpers + `check_freshness` |
| | `src/ats/ingestion/universe.py` | M1 universe + xvenue mapping loader |
| CLI | `ats db migrate/downgrade` | alembic wrappers |
| | `ats ingest backfill/xvenue-funding/media-pull` | Tier 1 one-shot commands |
| | `ats ingest start` | M4 stub (exits 2) |
| | `ats data status` | heartbeats + row counts |
| | `ats data validate` | programmatic smoke test |
| | `ats data summary` | backfill coverage by symbol/timeframe *(extends spec)* |
| | `ats data candles --viz` | OHLCV table + single-candle ASCII chart *(extends spec)* |
| Seeds | `seeds/universe_m1.yaml` | 5 majors (BTC/ETH/SOL + 2) |
| | `seeds/xvenue_symbols.yaml` | Bybit/OKX/Hyperliquid symbol mappings |
| | `seeds/rss_feeds.yaml`, `seeds/x_accounts.yaml` | media sources |
| Infra | `ops/docker-compose.yml`, `ops/init.sql` | TimescaleDB + extensions |
| Tests | `tests/test_*.py` (9 files) | 23 passed, 3 skipped (DB-dependent) |

### To close spec 01

```bash
docker compose -f ops/docker-compose.yml up -d
uv run ats db migrate
uv run ats ingest backfill --since 7d --symbols BTCUSDT,ETHUSDT
uv run ats ingest xvenue-funding --symbols BTCUSDT,ETHUSDT
uv run ats ingest media-pull
uv run ats data status
uv run ats data validate       # must exit 0
```

Then tick all acceptance criteria in `specs/01-data-collection.md` and update this table to ✅.

---

## Read these first

1. [`architecture.md`](architecture.md) — the vision, the hard truth, principles, anti-goals
2. [`specs/00-roadmap.md`](specs/00-roadmap.md) — milestone map, validation philosophy, cumulative state
3. [`specs/01-data-collection.md`](specs/01-data-collection.md) — start here when implementing

---

## Prerequisites

- Python 3.12+ (managed by [uv](https://docs.astral.sh/uv/))
- Docker (Postgres for M1; Redis added in M4)
- An Anthropic API key — **only from M2 (spec 07) onward**
- An X/Twitter API token — **only from M2**, and even then optional

---

## Quickstart (Phase 0 baseline)

```bash
uv sync
uv run ats --version
```

That's the entire baseline. Each spec adds its own deps, tables, and CLI
commands. See [`specs/00-roadmap.md`](specs/00-roadmap.md) for the cumulative
dependency matrix.

### Spec 01 quickstart (Tier 1 / M1)

```bash
cp .env.example .env              # set DATABASE_URL; leave X_BEARER_TOKEN blank
docker compose -f ops/docker-compose.yml up -d
uv run ats db migrate
uv run ats ingest backfill --since 7d --symbols BTCUSDT,ETHUSDT
uv run ats ingest xvenue-funding --symbols BTCUSDT,ETHUSDT
uv run ats ingest media-pull
uv run ats data status
uv run ats data validate          # exits 0 on success
```

See [`specs/01-data-collection.md`](specs/01-data-collection.md) for the full
acceptance checklist and Tier 3 smoke test.

---

## How to work a spec

1. Open the spec file. Confirm the milestone and that prior specs are ✅.
2. Run its `uv add ...` commands; apply infra changes if any.
3. Implement the components listed in the spec.
4. Run the spec's **Smoke test**.
5. Tick the spec's **Acceptance criteria**.
6. Add the pytest cases to `tests/`.
7. Run `uv run ats <spec> validate`.
8. Commit with tag `spec-N: done`.
9. **Update the status tables in this README.**

Do not start spec N+1 until spec N's acceptance criteria all pass. **Do not start
M2 until `ats gate check` (spec 06) returns GO.**

---

## Repo layout

```text
agent-orchestration/
├── architecture.md         # the vision + the hard truth
├── README.md               # this file — the live progress tracker
├── specs/                  # the actionable plans, M1 → M4
│   ├── 00-roadmap.md
│   ├── 01-data-collection.md      ┐
│   ├── 02-data-processing.md      │
│   ├── 03-orchestration.md        │ M1 — Prove Edge
│   ├── 04-deterministic-signal.md │
│   ├── 05-replay-harness.md       │
│   ├── 06-decision-gate.md        ┘
│   ├── 07-llm-layer.md            ┐ M2 — Sharpen
│   ├── 08-learning.md             ┘
│   ├── 09-interface-ui-mcp.md       M3 — Observe
│   └── 10-live-operations.md        M4 — Operate
├── pyproject.toml          # grows per spec
├── src/ats/                # grows per spec
├── ops/                    # docker-compose, init.sql — added in spec 01
├── seeds/                  # universe / categories / feeds — per spec
├── alembic/                # added in spec 01
├── data/                   # gitignored — replay reports, gate rationale, cycle artifacts
├── reports/                # weekly reflection markdown — added in spec 08
├── ui/                     # Next.js dashboard — added in spec 09
└── tests/                  # golden fixtures + per-spec tests
```

---

## Development commands

```bash
uv sync                     # install / update deps
uv run ats --help           # current command surface
uv run ruff check .         # lint
uv run mypy src             # type check
uv run pytest               # unit tests
```

---

## Stack (locked at the minimum needed)

| Layer | Choice | First used |
|---|---|---|
| Language / package manager / CLI | Python 3.12+ / uv / Typer | baseline |
| Config / logging | pydantic-settings / structlog + rich | baseline |
| Database | Postgres 16 + TimescaleDB + pgvector | spec 01 (pgvector from spec 08) |
| LLM | Claude Haiku / Sonnet / Opus — tiered by call site (see [architecture.md → LLM model tiering](architecture.md)) via `anthropic` | spec 07 (M2) |
| Browser automation (optional) | Playwright | spec 07 (M2) |
| API / dashboard | FastAPI + uvicorn / Next.js 15 + Tailwind + shadcn | spec 09 (M3) |
| MCP | stdio + SSE transports | spec 09 (M3) |
| Cache / queue | Redis 7 + arq | spec 10 (M4) |

Each spec adds only what it needs. See [`specs/00-roadmap.md`](specs/00-roadmap.md).

---

## License

Private. All rights reserved.
