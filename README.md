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
> **Current spec:** *none started — begin with [`specs/01-data-collection.md`](specs/01-data-collection.md)*
> **Decision gate (spec 06):** not yet evaluated

*Update the three lines above and the status tables below as work progresses.*

---

## Milestone progress

| Milestone | Specs | Status | Gate to exit |
|---|---|---|---|
| **M1 — Prove Edge** | 01–06 | 🔲 Not started | `ats gate check` returns **GO** (spec 06) |
| **M2 — Sharpen** | 07–08 | 🔲 Not started | M2 replay beats the M1 baseline; learnings accumulate |
| **M3 — Observe** | 09 | 🔲 Not started | A coding agent can drive the system; dashboard renders a live cycle |
| **M4 — Operate** | 10 | 🔲 Not started | System runs unattended at the chosen tier |

**The one rule:** do not start M2 until M1's decision gate is green.

---

## Spec status

Legend: 🔲 Not started · 🏗️ In progress · ✅ Done · ⛔ Blocked

| Spec | Milestone | Status | Gate (all acceptance criteria pass) |
|---|---|---|---|
| [01 — Data Collection](specs/01-data-collection.md) | M1 | 🔲 | `ats data validate` green; 120d candles for ~5 majors |
| [02 — Data Processing](specs/02-data-processing.md) | M1 | 🔲 | `ats process validate` green; `pr_*` ∈ [0,1]; no look-ahead |
| [03 — Orchestration](specs/03-orchestration.md) | M1 | 🔲 | `ats screen validate` green; signal state machine + reconciliation correct |
| [04 — Deterministic Signal](specs/04-deterministic-signal.md) | M1 | 🔲 | `ats cycle validate` green; four agents + synthesizer; no LLM dependency |
| [05 — Replay Harness](specs/05-replay-harness.md) | M1 | 🔲 | `ats replay validate` green; no look-ahead; deterministic; baselines present |
| [06 — Decision Gate](specs/06-decision-gate.md) | M1 | 🔲 | `ats gate check` runs; verdict recorded in `data/gate_<date>.md` |
| [07 — LLM Layer](specs/07-llm-layer.md) | M2 | 🔲 | `ats llm validate` green; ±0.20 clamp holds; M2 replay ≥ M1 baseline |
| [08 — Learning](specs/08-learning.md) | M2 | 🔲 | `ats reflect validate` green (8a); 8b only after ≥40 closed session trades |
| [09 — Interface: UI & MCP](specs/09-interface-ui-mcp.md) | M3 | 🔲 | `ats serve/skills/mcp validate` green; CLI-parity + prompt-parity pass |
| [10 — Live Operations](specs/10-live-operations.md) | M4 | 🔲 | `ats ops validate` green; promotion checklist all ticked |

**Decision gate verdict:** *not yet evaluated* — see `data/gate_<date>.md` once spec 06 runs.

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
