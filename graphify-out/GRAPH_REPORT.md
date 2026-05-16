# Graph Report - agent-orchestration  (2026-05-16)

## Corpus Check
- 17 files · ~30,688 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 409 nodes · 390 edges · 25 communities (21 shown, 4 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 2 edges (avg confidence: 0.9)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `4ae64f19`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_CLI Entrypoint Internals|CLI Entrypoint Internals]]
- [[_COMMUNITY_Pipeline Phases 1-5|Pipeline Phases 1-5]]
- [[_COMMUNITY_Package + Analysis Skills|Package + Analysis Skills]]
- [[_COMMUNITY_Top-Level Docs|Top-Level Docs]]
- [[_COMMUNITY_MCP Server & UISkills Phases|MCP Server & UI/Skills Phases]]
- [[_COMMUNITY_System Rationale|System Rationale]]
- [[_COMMUNITY_Test Package Init|Test Package Init]]
- [[_COMMUNITY_Post-Mortem Skill|Post-Mortem Skill]]
- [[_COMMUNITY_Weekly Reflection Skill|Weekly Reflection Skill]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]

## God Nodes (most connected - your core abstractions)
1. `Agentic Trading System — Architecture` - 17 edges
2. `Spec 07 — LLM Layer · Milestone M2` - 15 edges
3. `Spec 03 — Orchestration · Milestone M1` - 14 edges
4. `Spec 09 — Interface: UI, MCP & Skills · Milestone M3` - 14 edges
5. `Spec 05 — Replay Harness · Milestone M1` - 13 edges
6. `Spec 04 — Deterministic Signal · Milestone M1` - 13 edges
7. `Spec 08 — Learning · Milestone M2` - 13 edges
8. `Agentic Trading System (ATS)` - 12 edges
9. `Spec 10 — Live Operations · Milestone M4` - 12 edges
10. `Spec 01 — Data Collection · Milestone M1` - 12 edges

## Surprising Connections (you probably didn't know these)
- `/cycle-now Skill` --calls--> `ATS CLI Entrypoint`  [INFERRED]
  specs/03-orchestration.md → src/ats/cli.py
- `/analyze-symbol Skill` --calls--> `ATS CLI Entrypoint`  [INFERRED]
  specs/04-deep-analysis.md → src/ats/cli.py
- `README Document` --references--> `Architecture Document`  [EXTRACTED]
  README.md → architecture.md
- `Architecture Document` --references--> `Specs Overview`  [EXTRACTED]
  architecture.md → specs/00-overview.md
- `README Document` --references--> `Specs Overview`  [EXTRACTED]
  README.md → specs/00-overview.md

## Communities (25 total, 4 thin omitted)

### Community 0 - "CLI Entrypoint Internals"
Cohesion: 0.06
Nodes (31): Acceptance criteria, `ats screen validate`, Attention scoring, CLI added, code:bash (uv add 'redis[hiredis]' arq), code:text (ats session run                         # Tier 1 master comm), code:bash (# Phase 1+2 must be running and have ≥7d of features), code:block12 (cycle 2026-05-12T14:00Z   regime=bull-low) (+23 more)

### Community 1 - "Pipeline Phases 1-5"
Cohesion: 0.06
Nodes (32): Acceptance criteria, API endpoints (read-only), `ats serve validate` / `ats skills validate` / `ats mcp validate`, Body sections (fixed order, so prompt extraction is mechanical), CLI added, CLI parity test pattern, code:bash (uv add fastapi 'uvicorn[standard]'), code:bash (mkdir ui && cd ui) (+24 more)

### Community 2 - "Package + Analysis Skills"
Cohesion: 0.06
Nodes (28): Acceptance criteria, Algorithms, `ats process validate`, CLI added, code:bash (uv add pandas numpy pandas-ta), code:python (def percentile_rank(series: pd.Series, lookback: int) -> pd.), code:python (def momentum_composite(rsi: float, macd_hist: float, roc_5: ), code:text (ats process run                        # one-shot: compute f) (+20 more)

### Community 3 - "Top-Level Docs"
Cohesion: 0.07
Nodes (29): Acceptance criteria, Agent specifics (the M2 additions), `ats llm validate`, CLI added, code:bash (uv add anthropic                          # Claude SDK with ), code:bash (uv run ats analyze BTCUSDT       # now shows llm_δ column, s), code:python (@dataclass), code:python (# M2 weights — re-weighted from spec 04's four-agent vector) (+21 more)

### Community 4 - "MCP Server & UI/Skills Phases"
Cohesion: 0.07
Nodes (26): Acceptance criteria — 8a, Acceptance criteria — 8b (when built), `ats reflect validate`, CLI added, code:bash (uv add pgvector                  # Python client; SQL extens), code:block4 (SETUP), code:python (def retrieve_relevant_learnings(setup_snapshot, k=3, min_con), code:text (# 8a) (+18 more)

### Community 5 - "System Rationale"
Cohesion: 0.07
Nodes (25): Acceptance criteria, `ats replay validate`, CLI added, code:block2 (ats replay run --since 90d:), code:block3 (Replay window: 2026-02-12 → 2026-05-13  (90d, ~5 symbols)), code:block4 (data/replay_2026-02-12_2026-05-13/), code:block5 (ats replay run --since 90d --ablate           # baseline + 8), code:block6 (ABLATION MATRIX (90d, ~5 symbols, baseline = all 8 agents)) (+17 more)

### Community 6 - "Test Package Init"
Cohesion: 0.07
Nodes (26): Acceptance criteria, `ats ops validate`, CLI added, code:bash (uv add 'redis[hiredis]' arq), code:yaml (redis:), code:cron (*/15 * * * *  cd /path/to/ats && uv run ats session run >> l), code:bash (docker compose -f ops/docker-compose.yml up -d        # Post), code:text (ats ingest start                     # WS + continuous polle) (+18 more)

### Community 7 - "Post-Mortem Skill"
Cohesion: 0.08
Nodes (24): Acceptance criteria, Agent contract, agent_runs (per-agent audit trail), `ats cycle validate`, CLI added, code:sql (ALTER TABLE signals), code:bash (uv run ats analyze BTCUSDT), code:block11 (agent           det     final    direction    notes) (+16 more)

### Community 8 - "Weekly Reflection Skill"
Cohesion: 0.1
Nodes (20): Binance REST, Binance WebSocket, CLI added, code:bash (uv add sqlalchemy[asyncio] asyncpg alembic), code:text (ats db migrate                       # alembic upgrade head), Components, Cross-venue funding REST (M1), Data sources (+12 more)

### Community 9 - "Community 9"
Cohesion: 0.1
Nodes (19): Agent inventory, Agentic Trading System — Architecture, Anti-goals, code:text (┌───────────────────────────────┐), Core principles, Definition of done, LLM model tiering, Operating tiers (+11 more)

### Community 10 - "Community 10"
Cohesion: 0.1
Nodes (19): code:bash (# One-time), code:bash (docker compose -f ops/docker-compose.yml up -d        # Post), Cross-spec conventions, Cumulative state at each spec, Errors, How to use these specs, Idempotency, Milestones (+11 more)

### Community 11 - "Community 11"
Cohesion: 0.11
Nodes (10): basis (hypertable), candles (hypertable), Data model, funding_rates, funding_rates_xvenue, heartbeats, liquidations, mark_prices_1m (hypertable) (+2 more)

### Community 12 - "Community 12"
Cohesion: 0.12
Nodes (16): Acceptance criteria, `ats gate check`, `ats gate validate`, code:text (ats gate check [--window 90d]        # evaluate the gate aga), code:block2 (DECISION GATE — 2026-05-13  (replay window 2026-02-12 → 2026), Components, Dependencies on prior specs, Goal (+8 more)

### Community 13 - "Community 13"
Cohesion: 0.12
Nodes (15): Agentic Trading System (ATS), code:bash (uv sync), code:text (agent-orchestration/), code:bash (uv sync                     # install / update deps), Development commands, How to work a spec, License, Milestone progress (+7 more)

### Community 14 - "Community 14"
Cohesion: 0.15
Nodes (13): Agent specifics (deterministic cores only), Basis (perp premium over spot index), code:text (inputs:  recent_ohlcv (4h primary), atr_4h, current_close, s), code:text (inputs:  features.funding_divergence, features.funding_diver), code:text (inputs:  features.basis_premium, features.basis_z_30d), code:text (prereq: candles.taker_buy_vol exists (added in spec 01)), CrossVenueFlow (cross-exchange funding divergence), CVD (cumulative volume delta divergence) (+5 more)

### Community 15 - "Community 15"
Cohesion: 0.22
Nodes (11): Architecture Document, MCP Server, README Document, Specs Overview, Phase 1: Data Collection, Phase 2: Data Processing, Phase 3: Orchestration, Phase 4: Deep Analysis (+3 more)

### Community 16 - "Community 16"
Cohesion: 0.2
Nodes (10): Acceptance criteria — Tier 1, Acceptance criteria — Tier 3 (only required when promoting), `ats data validate`, code:bash (# 1. Postgres up (local, Neon, or Supabase free tier)), code:block13 (data_class              status         last_seen           r), code:bash (docker compose -f ops/docker-compose.yml up -d        # Post), pytest, Tier 1 smoke test — under 2 minutes (+2 more)

### Community 17 - "Community 17"
Cohesion: 0.22
Nodes (7): code:sql (ALTER TABLE agent_runs), code:sql (ALTER TABLE paper_trades ADD COLUMN narrative_ids UUID[] NOT), Data model, Extend signals + agent_runs, llm_costs, narratives + narrative_items, paper_trades.narrative_ids

### Community 18 - "Community 18"
Cohesion: 0.4
Nodes (3): main_callback(), ATS CLI — baseline entrypoint.  Each phase extends this module with its own comm, No-op root callback; phase commands are registered here.

### Community 19 - "Community 19"
Cohesion: 0.5
Nodes (4): ATS CLI Entrypoint, ATS Package, /analyze-symbol Skill, /cycle-now Skill

## Knowledge Gaps
- **239 isolated node(s):** `Agentic Trading System.  See `architecture.md` at the repo root for the high-lev`, `ATS CLI — baseline entrypoint.  Each phase extends this module with its own comm`, `No-op root callback; phase commands are registered here.`, `What this is`, `The hard truth (read this before anything else)` (+234 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Spec 01 — Data Collection · Milestone M1` connect `Weekly Reflection Skill` to `Community 16`, `Community 11`?**
  _High betweenness centrality (0.011) - this node is a cross-community bridge._
- **Why does `Data model` connect `Community 11` to `Weekly Reflection Skill`?**
  _High betweenness centrality (0.008) - this node is a cross-community bridge._
- **Why does `Spec 07 — LLM Layer · Milestone M2` connect `Top-Level Docs` to `Community 17`?**
  _High betweenness centrality (0.008) - this node is a cross-community bridge._
- **What connects `Agentic Trading System.  See `architecture.md` at the repo root for the high-lev`, `ATS CLI — baseline entrypoint.  Each phase extends this module with its own comm`, `No-op root callback; phase commands are registered here.` to the rest of the system?**
  _239 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `CLI Entrypoint Internals` be split into smaller, more focused modules?**
  _Cohesion score 0.06 - nodes in this community are weakly interconnected._
- **Should `Pipeline Phases 1-5` be split into smaller, more focused modules?**
  _Cohesion score 0.06 - nodes in this community are weakly interconnected._
- **Should `Package + Analysis Skills` be split into smaller, more focused modules?**
  _Cohesion score 0.06 - nodes in this community are weakly interconnected._