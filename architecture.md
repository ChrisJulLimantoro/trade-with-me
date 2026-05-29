# Agentic Trading System — Architecture

**Version:** v0.4 · **Status:** living document · **Companion:** `specs/` for actionable per-milestone plans · `README.md` for live progress

---

## Build status (as of 2026-05-26)

**Milestone:** M1 — Prove Edge · **Active spec:** 01 — Data Collection

| Spec | Status | Notes |
|---|---|---|
| 01 — Data Collection | 🏗️ code + tests complete | `ats db migrate` bug fixed; DB smoke test still needed to close |
| 02–06 — M1 remaining | 🔲 not started | blocked on spec 01 acceptance criteria |
| 07–10 — M2–M4 | 🔲 not started | gated on M1 decision gate |

**What is running:**
- `ats db migrate/downgrade` — Alembic wrappers
- `ats ingest backfill / xvenue-funding / media-pull` — Tier 1 one-shot REST ingestion
- `ats data status / validate / summary / candles` — data inspection CLI
- All 9 spec-01 pytest files: **23 passed, 3 skipped** (skips are DB-dependent)

**What is NOT yet wired (future specs):**
- Any indicator/feature computation (spec 02)
- Signal agents or synthesizer (spec 04)
- Replay harness (spec 05)
- Decision gate (spec 06)
- LLM calls, learning loop, dashboard, live daemons (M2–M4)

The live tracker with the full acceptance checklist lives in `README.md`.

---

> v0.4 reorganizes the build around **four milestones (M1–M4)** with a hard
> **decision gate** after M1. The earlier versions described the *end state*
> well but sequenced the work as a six-phase commitment you couldn't validate
> until the end. v0.4 fixes the ordering: prove the signal has edge on the
> cheapest possible deterministic slice *before* building the LLM swarm, the
> learning loop, the dashboards, or the always-on infrastructure.

---

## What this is

A signal generator for crypto perpetual futures.

It ingests Binance market data, **cross-venue derivatives data** (funding rates
from Bybit / OKX / Hyperliquid, perp-vs-spot basis), and crypto media; turns the
raw stream into normalized features; screens the universe for high-attention coins;
runs a small swarm of narrow deterministic analyzers on the top picks; emits a
paper-traded signal with entry / stop / target / confidence / reasoning; watches the
outcome; and learns from it.

The system **only trades on Binance**, but reads from multiple venues to detect
asymmetric positioning (e.g. Binance funding ≫ Hyperliquid funding → Binance longs
are crowded). Multi-venue execution is permanently out of scope.

**It does not execute trades.** It is an attention engine and a structured
signal producer that runs forward in paper mode while it earns trust.

---

## The hard truth (read this before anything else)

This system is **three stacked ambitions**, and they are easy to confuse for
one. They must be built in this order:

1. **A trading signal that has edge.** ← the only thing that matters for v1.
   Everything else is worthless if this isn't true.
2. **A self-learning loop.** ← a genuine moat *if* #1 holds; expensive theater
   if it doesn't.
3. **An "agentic OS" — skills, CLI parity, MCP, portable prompts.** ← elegant
   infrastructure that carries **zero edge** on its own. It makes a working
   system pleasant to operate. It cannot make a non-working system work.

The failure mode this architecture is designed to prevent: spending two months
building #2 and #3 beautifully, then discovering #1 was never there. So the
build is sequenced to **answer "does the signal have edge?" as fast and as
cheaply as possible** — deterministic math only, ~5 symbols, no LLM spend, a
replay harness instead of a three-month forward wait — and to **gate**
everything downstream on that answer.

If M1 fails its decision gate, you have lost doc time and a few days of code.
If you skip the gate, you can lose a quarter.

---

## Core principles

1. **Edge first, infrastructure last.** Prove the signal before building the
   things that orchestrate, narrate, observe, or daemonize it.

2. **Data first, agents second.** The hard work is making the data complete,
   fresh, normalized, and replayable. Agents are just reducers on clean data.
   If the data is wrong, no agent can save you.

3. **Deterministic before probabilistic.** Every signal must trace back to
   numbers, not LLM vibes. The entire M1 signal is deterministic. LLMs enter in
   M2 only to narrate, classify media, or adjudicate fuzzy cases — and even then
   their influence on confidence is hard-clamped to **±20%**.

4. **The decision gate is real.** M1 ends with a numeric go/no-go
   (`specs/06-decision-gate.md`). "No-go" means loop back into the deterministic
   layer — it does not mean "build M2 anyway and hope."

5. **Replay over waiting.** A replay harness that runs the pipeline over 90 days
   of history turns a ~3-month forward-paper feedback loop into a ~1-day one.
   This is *not* backtesting-driven optimization (still an anti-goal) — it is
   validation. You replay to *measure*, not to *tune*.

6. **No "talking" agents — and agent prompts are versioned files.** Agents do
   not chat with each other. Each reads the same structured inputs and emits a
   scalar score with metadata. The synthesizer combines them. From M2 onward an
   agent's system prompt lives in `.claude/skills/<name>/SKILL.md` — one source
   of truth — not in a Python string literal.

7. **CLI throughout, skills and web at the end.** Every artifact is inspectable
   from the terminal from day one. The CLI is the canonical interface for M1–M2.
   SKILL.md wrappers and the dashboard come in M3 — once the CLI surface is
   stable. Don't build the CLI and the skill surface in lockstep.

8. **Per-milestone reproducibility.** Each spec is a vertical slice with declared
   inputs, outputs, acceptance criteria, and a smoke test. You can stop at any
   spec and have a working artifact.

9. **No upfront bootstrap.** Each spec adds only the deps, tables, and CLI it
   actually needs. The kitchen sink is never imported.

10. **Idle cost is zero by default.** The system runs with **zero** background
    processes in its default mode. Always-on services are an opt-in tier (M4),
    gated behind validation. The same code does session-mode and live-mode work;
    you just stop invoking the daemon entry points.

---

## What the LLM is NOT for

Principles 2 and 3 say what the LLM *is* for. This is the negative space — the
boundary is load-bearing, so it is stated explicitly. A tempting wrong idea is
"use a cheap model to collect data, a strong model to synthesize." Both halves
break the system:

- **The LLM never touches ingestion / data collection.** That path is
  deterministic I/O: fetch from Binance / Bybit / OKX / Hyperliquid REST, parse
  JSON, `INSERT ... ON CONFLICT`. An LLM in that path can *hallucinate a candle*
  or a funding rate, adds latency and cost, and destroys idempotency — which the
  replay harness (spec 05) and the decision gate (spec 06) completely depend on.
  There is no judgment task here. The only LLM contact with media is *classifying
  already-collected* `media_items` rows (sentiment / narrative) — never the
  collection itself. The same rule applies to the new cross-venue funding,
  `premiumIndex`, and `taker_buy_vol` feeds: pure REST, pure SQL, no LLM.

- **The LLM is never the synthesizer.** The synthesizer is arithmetic over agent
  scores — direction vote, weighted mean, alignment penalty, regime modulation,
  RR floor. It is deterministic *on purpose*. An LLM synthesizer cannot be
  replayed deterministically → spec 05 breaks → spec 06 can't measure edge → the
  entire M1 validation strategy collapses. You also lose auditability and inherit
  the LLM "always-agree" confidence inflation.

What the LLM *is* for, restated in one line: narrate `reasons[]`, classify media,
and adjudicate genuinely ambiguous deterministic cases — always inside the ±0.20
clamp, never setting direction, never inventing a number.

---

## The milestones

| Milestone | Specs | Purpose | Closes when |
|---|---|---|---|
| **M1 — Prove Edge** | 01–06 | Cheapest end-to-end deterministic signal over ~5 majors, exercised by a replay harness. | `06-decision-gate.md` returns **go**. |
| **M2 — Sharpen** | 07–08 | Add the LLM ±0.20 layer, sentiment/narrative, expand the universe to ~50 + diversity filter, add the learning loop. | Signal quality improves measurably over the M1 baseline; learnings accumulate. |
| **M3 — Observe** | 09 | Read-only FastAPI + Next.js dashboard + MCP server + the SKILL.md / CLI-parity contract layer. | Any coding agent can drive the system; the dashboard renders a live cycle. |
| **M4 — Operate** | 10 | Promote to Tier 2 (cron) then Tier 3 (live daemons). The "final picture." | The system runs unattended at the chosen tier. |

**M1 is a gate, not a phase.** Do not start M2 until M1's decision gate is green.

The full progress tracker lives in `README.md`.

---

## Definition of done

The milestone gates say when each *milestone* is finished. This says what the
*whole system* looks like when it has succeeded — a measurable target, distinct
from the per-milestone gates. It is a living target, not a terminal state.

The system has succeeded when **all** of the following hold:

1. **All four milestone gates are green** — with the spec-06 decision gate (GO)
   as the load-bearing one.
2. **Forward expectancy is real.** Live paper-trade expectancy (not replay) is
   positive and statistically consistent with the spec-06 replay estimate, over
   **≥ 100 closed live signals**. Forward ≈ replay, within noise.
3. **The learning loop is closed.** Spec 08's 8b retrieval is demonstrably
   surfacing relevant past learnings into synthesis — measured as a non-zero,
   non-random retrieval-hit rate in the weekly reflection.
4. **It runs unattended.** The system operates at Tier 2 or Tier 3 with no manual
   intervention and zero freshness-watchdog gaps over a sustained window
   (≥ 2 weeks).
5. **It is drivable by any agent.** A coding agent (Claude Code, OpenCode,
   Cursor) can query the system read-only via the skill set + MCP server.
6. **It stays within budget.** LLM cost per cycle remains under the spec-07
   budget at the full ~50-symbol universe.

If criterion 2 fails, nothing else matters — the system is a well-engineered way
to lose paper money. That is why M1 exists: to find that out in weeks, not after
the whole thing is built.

---

## Two runtimes

Work splits into two kinds, each with its own runtime:

| Concern | Runtime | Notes |
|---|---|---|
| Binance REST ingestion (M1), WS streaming + watchdog (M4) | **Python service** | M1 uses one-shot REST backfill. WS is M4 only. |
| Indicator + regime computation | **Python** — fired synchronously inside a session (M1); event-driven (M4) | Same math, different trigger. |
| Screening, diversity, signal state machine | **Python** — `ats session run` (M1); arq worker (M4) | A `/cycle-now` skill wraps it in M3. |
| Deterministic analyzers + synthesizer | **Python** (M1) | The whole M1 signal. No LLM. |
| LLM-assisted agents (sentiment, narrative, hybrid deltas) | **Python**, prompts in `SKILL.md` (M2) | Skill-shaped invocation added in M3. |
| Operator workflows (analyze a symbol, post-mortem, reflection) | **Skills + CLI twins** (M3) | On-demand, narrative outputs, versioned prompts. |
| Read-only HTTP API + dashboard + MCP server | **Service** (M3) | Same Postgres reads, multiple transports. |
| Mark-price polling + tick-precise SL/TP closer | **Python service** (M4) | M1–M3 use bar-conservative reconciliation. |

The **Skill ↔ CLI parity** rule (introduced in M3): every on-demand surface ships
as both a skill and a `ats <verb>` CLI command backed by the same Python entry
point. Deterministic math, Pydantic validation, the ±0.20 clamp, the cost guard,
and DB writes always live in Python — a `SKILL.md` never contains math.

---

## Agent inventory

The deterministic signal (spec 04) is produced by **eight narrow agents**, each a
pure-Python scoring function that takes structured inputs and emits a scalar score
+ direction. The synthesizer combines them with hand-set, version-controlled
weights. No agent uses an LLM in M1.

This table is the at-a-glance contract: what each agent reads, what it emits, how
much it weighs in the synthesis, and where it shines. Full per-agent logic
(pseudocode, invalidation rules, metadata) lives in `specs/04-deterministic-signal.md`.

| Agent | Thesis (one line) | Inputs | Output | M1 weight | Regime affinity | Notes |
|---|---|---|---|---|---|---|
| **Structure** | Breakout/breakdown beyond N-bar high with volume confirmation | candles | score, direction | 0.25 | trend regimes | Existing core. M2 gains LLM ±0.20 delta on ambiguous cases (spec 07). |
| **Momentum** | RSI + MACD on 4h confirms direction of the move | features | score, direction | 0.15 | trend regimes | Existing core. |
| **Funding** | Extreme funding-z = the crowd is positioned → fade | funding rates, OI features | score, direction | 0.10 | any | Existing core. |
| **Liquidity** | Volume-weighted swing clusters as proxy for stop pools | candles | score, direction | 0.05 | any | M1 proxy; full liquidation-cluster version requires the M4 WS feed. |
| **PriceAction** *(new)* | Fresh Fair Value Gap aligned with direction; gap edges supply a refined entry zone | candles | score, direction, `fvg_zone` | 0.15 | any (best in trend) | The only agent that overrides the synthesizer's default entry zone. FVG hold-rate prior ~60% from published forex/index studies; treat as a starting hypothesis until ablation confirms. |
| **CrossVenueFlow** *(new)* | Persistent funding divergence between Binance and peer CEXs reveals positioning asymmetry → bias against the crowded venue | Binance + Bybit + OKX + Hyperliquid funding REST | score, direction | 0.15 | any | The system's most distinctive data edge — the median retail trader doesn't pull cross-venue funding. REST-only, $0 idle. |
| **Basis** *(new)* | Perp premium over spot index at a 30d percentile extreme → fade | Binance `premiumIndex` REST | score, direction | 0.10 | range / extremes | Cheap signal cousin of the spot-perp basis trade. |
| **CVD** *(new)* | Price/CVD divergence (price NH, CVD doesn't) signals hidden distribution / accumulation | `candles.taker_buy_vol` (new column) | score, direction | 0.05 | any | Foundational crypto order-flow signal. Adds one column to `candles`. |

Weights sum to **1.00** and live in `src/ats/orchestration/weights.py` as named
constants. The **anti-tuning rule applies**: weights stay fixed across replay runs.
The decision gate (spec 06) may *drop* an agent whose ablation contribution
is ≤ 0 (per spec 05's ablation matrix), but it never *retunes* the survivors.

When PriceAction's score is high and its direction matches the synthesized direction,
the synthesizer replaces the default entry zone (current close ± ATR band) with the
FVG zone edges. This is the only place a single agent influences signal *shape*
rather than just *confidence*.

The four new agents are deliberately deterministic and REST-only. None of them
require WebSockets, always-on processes, or LLM calls, so they fit Tier 1
(`$0` idle) without compromise.

---

## Operating tiers

The same code base supports three tiers. You start at Tier 1 and only promote
when the prior tier has earned trust. The *implementation* is identical — only
the invocation differs.

| Tier | Idle cost | How it runs | Outcome-tracking | Use when |
|---|---|---|---|---|
| **1 — Session** (default) | **$0** | Manual: `ats session run`, or a skill from a coding agent | Bar-conservative (replay candle highs/lows since last session) | M1–M3; validating; budget is tight |
| **2 — Scheduled** | DB only | Cron calls `ats session run` every 15m / 4h | Better than Tier 1, worse than Tier 3 | M4; stable signal, want unattended ops without daemons |
| **3 — Live** | DB + Redis + 2–3 daemons | `ats serve --live` promotes the same coroutines into long-running tasks | Tick-precise | M4; after the decision gate *and* M2 learning data *and* budget |

What you lose by **not** running 24/7 (so the trade-off is explicit): tick-precise
SL/TP tracking (mitigated by conservative bar reconciliation), reaction to 3am
funding spikes (acceptable during validation), a continuous X stream (free tier
is daily-capped anyway), a live "ticker" feel (worthless until you trust the
system). None of these block M1–M3.

---

## The pipeline

```text
                   ┌───────────────────────────────┐
                   │ Binance REST       (M1)       │ klines (+taker_buy_vol),
                   │                               │   funding, OI, premiumIndex
                   │ Bybit / OKX /      (M1)       │ funding REST (cross-venue
                   │   Hyperliquid REST            │   divergence signal)
                   │ News RSS           (M1)       │ media items
                   │ Binance WS         (M4)       │ + markPrice, liquidations
                   │ X / Twitter        (M2)       │ + curated accounts
                   └─────────┬─────────────────────┘
                             ▼  SPEC 01 — DATA COLLECTION
                   ┌───────────────────────┐
                   │ Postgres / Timescale  │
                   └─────────┬─────────────┘
                             ▼  SPEC 02 — DATA PROCESSING
                   ┌───────────────────────┐
                   │ indicators · regime   │  ATR·RSI·MACD·EMA·OBV·CVD
                   │ percentile-rank norm  │  funding_divergence_z · basis_z
                   │                       │  BTC trend × vol percentile
                   └─────────┬─────────────┘
                             ▼  SPEC 03 — ORCHESTRATION
                   ┌───────────────────────┐
                   │ outcome reconciliation│  attention score
                   │ signal state machine  │  (diversity filter → M2)
                   └─────────┬─────────────┘
                             ▼  SPEC 04 — DETERMINISTIC SIGNAL
                   ┌───────────────────────┐
                   │ 8 deterministic agents│  Structure · Momentum
                   │ + synthesizer         │  Funding · Liquidity
                   │                       │  PriceAction (FVG) · CrossVenueFlow
                   │                       │  Basis · CVD
                   │                       │  → entry/sl/tp/confidence
                   └─────────┬─────────────┘   { entry, sl, tp, confidence }
                             ▼  SPEC 05 — REPLAY HARNESS
                   ┌───────────────────────┐
                   │ run pipeline over 90d │  → N closed paper trades
                   │ of history            │  → hit rate, expectancy
                   └─────────┬─────────────┘
                             ▼  SPEC 06 — DECISION GATE  ◀── go / no-go
        ──────────────────────────────────────────────────────────────
          no-go → loop back to 02–04        go ↓
        ──────────────────────────────────────────────────────────────
                             ▼  SPEC 07 — LLM LAYER  (M2)
                   ┌───────────────────────┐
                   │ sentiment · narrative │  + hybrid ±0.20 deltas
                   │ vision · MarketMetrics│  + universe → ~50 + diversity
                   └─────────┬─────────────┘
                             ▼  SPEC 08 — LEARNING  (M2)
                   ┌───────────────────────┐
                   │ paper-trade journal   │  weekly reflection
                   │ post-mortem → memory  │  pgvector retrieval → 8b
                   └─────────┬─────────────┘
                             ▼  SPEC 09 — INTERFACE  (M3)
                   ┌───────────────────────┐
                   │ FastAPI · Next.js     │  read-only
                   │ MCP server · skills   │  CLI-parity contract
                   └─────────┬─────────────┘
                             ▼  SPEC 10 — LIVE OPERATIONS  (M4)
                   ┌───────────────────────┐
                   │ cron → Tier 2         │  WS streaming, watchdog,
                   │ daemons → Tier 3      │  mark-price poller
                   └───────────────────────┘
```

---

## Spec map (one line each)

| # | Spec | Milestone | Delivers |
|---|---|---|---|
| 0 | [Roadmap](specs/00-roadmap.md) | — | milestone map, validation philosophy, cumulative state, conventions |
| 1 | [Data Collection](specs/01-data-collection.md) | M1 | Binance REST + RSS ingestion over ~5 majors; hypertables |
| 2 | [Data Processing](specs/02-data-processing.md) | M1 | per-bar normalized features + global regime tags |
| 3 | [Orchestration](specs/03-orchestration.md) | M1 | attention ranking, signal state machine, outcome reconciliation |
| 4 | [Deterministic Signal](specs/04-deterministic-signal.md) | M1 | eight deterministic agents (Structure / Momentum / Funding / Liquidity / PriceAction / CrossVenueFlow / Basis / CVD) + synthesizer → structured signal |
| 5 | [Replay Harness](specs/05-replay-harness.md) | M1 | run the pipeline over 90d history → hit-rate report |
| 6 | [Decision Gate](specs/06-decision-gate.md) | M1 | numeric go/no-go on whether the signal has edge |
| 7 | [LLM Layer](specs/07-llm-layer.md) | M2 | LLM agents, ±0.20 hybrid deltas, vision, universe expansion + diversity |
| 8 | [Learning](specs/08-learning.md) | M2 | paper-trade outcomes → reflection; pgvector memory (sub-stage 8b) |
| 9 | [Interface: UI & MCP](specs/09-interface-ui-mcp.md) | M3 | read-only FastAPI + dashboard + MCP server + skill/CLI-parity contract |
| 10 | [Live Operations](specs/10-live-operations.md) | M4 | Tier 2 cron → Tier 3 daemons promotion playbook |

---

## Validation philosophy

We do not advance to spec N+1 until spec N's acceptance criteria all pass. We do
not advance to **M2** until the **decision gate** is green.

Every spec ships with:

- **Smoke test** — a sequence of CLI commands you can run in under 5 minutes
- **Acceptance criteria** — boolean checks; either all pass or the spec is not done
- **pytest unit tests** — golden-fixture-based regression protection
- **`ats <spec> validate`** — one command that runs the smoke test programmatically

If a spec's acceptance criteria can't be met within ~2 focused weeks, stop and
revisit the design.

---

## Scope constraints

**In scope**
- Binance USDT-margined perpetuals
- Timeframes: 15m, 1h, 4h
- Paper signals only (no execution)
- Single operator (no multi-user auth)
- M1 universe ~5 majors; M2+ universe ~50

**Out of scope (deferred)**
- Auto-execution / live order placement
- Multi-exchange routing
- Sub-minute / HFT
- Portfolio-level hedging or correlation
- Reinforcement-learning weight tuning (manual review until ≥100 closed signals)
- Public-facing web with auth

---

## Stack (locked at minimum)

- **Language:** Python 3.12+ · **Package manager:** uv · **CLI:** Typer
- **Database:** Postgres 16 + TimescaleDB + pgvector (pgvector used from spec 08).
  M1 runs fine on local Postgres or a free-tier Neon / Supabase; managed
  Timescale is only an M4 concern.
- **Cache / queue:** Redis 7 + arq — **M4 only**. Not required for M1–M3.
- **LLM:** Claude, **tiered by call site** (Haiku / Sonnet / Opus) via the
  `anthropic` SDK with prompt caching — **M2+**. See the model-tiering matrix
  below.
- **Browser automation (optional):** Playwright — M2, gated behind the per-cycle
  cost budget.
- **Agent runtime (optional):** any coding agent that reads `.claude/skills/` —
  M3+. The CLI always works without one.
- **MCP:** stdio + SSE transports — M3.
- **API / dashboard:** FastAPI + uvicorn / Next.js 15 + Tailwind + shadcn — M3.
- Each spec declares its own additional deps. See `specs/00-roadmap.md` for the
  cumulative dep matrix and tier annotations.

### LLM model tiering

There is no single "the model." Each LLM call site picks the cheapest model that
can do its job — cheap models for high-volume simple tasks, strong models for
rare hard ones. This matrix is canonical; specs 07 and 08 reference it.

| Call site | Spec | Model | Volume | Why |
|---|---|---|---|---|
| Sentiment (per symbol/cycle) | 07 | **Haiku** | high | one-symbol media classification — simple, repetitive |
| `reasons[]` rendering | 07 | **Haiku** | high | near-templating from already-finalized signal fields |
| Structure / Liquidity hybrid delta | 07 | **Sonnet** | medium (ambiguous cases only) | bounded chart/structure judgment inside the ±0.20 clamp |
| Narrative (cycle-level) | 07 | **Sonnet** | low (once per cycle) | 7-day media synthesis across the universe |
| Post-mortem (per closed trade) | 08 | **Opus** | low | genuinely hard causal reasoning about why a trade worked or didn't |
| Weekly reflection summary | 08 | **Opus** | very low (weekly) | high-value aggregate reasoning over the week's outcomes |

Exact IDs: Haiku → `claude-haiku-4-5`, Sonnet → `claude-sonnet-4-6`, Opus →
`claude-opus-4-7`. All calls use prompt caching. Model tiering — Haiku on the
high-volume calls — is the primary lever that keeps the spec-07 per-cycle cost
budget realistic at the full ~50-symbol universe.

---

## Anti-goals

- No agent-to-agent dialogue
- **No LLM in the deterministic path** — not in ingestion, not in feature
  compute, not in the synthesizer. See "What the LLM is NOT for".
- No premature abstraction (don't build a generic plugin system before two plugins)
- No upfront bootstrap of folders/configs/services you don't yet use
- No silent fallbacks — every degraded mode is logged and visible in `ats data status`
- **No backtesting-driven "optimization" before forward validation.** The replay
  harness (spec 05) *measures*; it does not *tune*. Weight tuning waits for ≥100
  closed signals.
- **No building M2 before the M1 decision gate is green.**
- **No always-on services by default** — Tier 3 is opt-in (M4); Tier 1 is the default
- **No self-evolving skills.** `SKILL.md` files are version-controlled prompts.
  The system never rewrites its own skills at runtime.
- **No write tools over MCP, ever.** The MCP surface (M3) is read-only and stays
  that way. Order placement is permanently out of scope.

---

## Where to start

Read `specs/00-roadmap.md`, then `specs/01-data-collection.md`. Work M1 in order
(01 → 06). Run each spec's smoke test, tick its acceptance boxes, update
`README.md`. **At spec 06, stop and run the decision gate.** Only a green gate
unlocks M2.
