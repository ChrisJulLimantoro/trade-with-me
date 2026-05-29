# Graph Report - agent-orchestration  (2026-05-26)

## Corpus Check
- 56 files · ~41,773 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 639 nodes · 672 edges · 51 communities (37 shown, 14 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 34 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `3f3b0eb2`
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
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]

## God Nodes (most connected - your core abstractions)
1. `Agentic Trading System — Architecture` - 18 edges
2. `Spec 07 — LLM Layer · Milestone M2` - 15 edges
3. `Spec 03 — Orchestration · Milestone M1` - 14 edges
4. `Spec 09 — Interface: UI, MCP & Skills · Milestone M3` - 14 edges
5. `Agentic Trading System (ATS)` - 13 edges
6. `Spec 05 — Replay Harness · Milestone M1` - 13 edges
7. `Spec 04 — Deterministic Signal · Milestone M1` - 13 edges
8. `Spec 08 — Learning · Milestone M2` - 13 edges
9. `Spec 10 — Live Operations · Milestone M4` - 12 edges
10. `Spec 01 — Data Collection · Milestone M1` - 12 edges

## Surprising Connections (you probably didn't know these)
- `test_align_8h_snaps_correctly()` --calls--> `_align_8h()`  [INFERRED]
  tests/test_xvenue_funding_parse.py → src/ats/ingestion/xvenue_funding.py
- `test_align_8h_already_aligned()` --calls--> `_align_8h()`  [INFERRED]
  tests/test_xvenue_funding_parse.py → src/ats/ingestion/xvenue_funding.py
- `test_btcusdt_okx_mapping()` --calls--> `load_xvenue_mapping()`  [INFERRED]
  tests/test_xvenue_symbol_mapping.py → src/ats/ingestion/universe.py
- `test_btcusdt_hyperliquid_mapping()` --calls--> `load_xvenue_mapping()`  [INFERRED]
  tests/test_xvenue_symbol_mapping.py → src/ats/ingestion/universe.py
- `test_missing_symbol_returns_none()` --calls--> `load_xvenue_mapping()`  [INFERRED]
  tests/test_xvenue_symbol_mapping.py → src/ats/ingestion/universe.py

## Communities (51 total, 14 thin omitted)

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
Cohesion: 0.05
Nodes (36): Acceptance criteria, Agent specifics (the M2 additions), `ats llm validate`, CLI added, code:bash (uv add anthropic                          # Claude SDK with ), code:bash (uv run ats analyze BTCUSDT       # now shows llm_δ column, s), code:sql (ALTER TABLE agent_runs), code:sql (ALTER TABLE paper_trades ADD COLUMN narrative_ids UUID[] NOT) (+28 more)

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
Cohesion: 0.05
Nodes (37): Acceptance criteria, Agent contract, agent_runs (per-agent audit trail), Agent specifics (deterministic cores only), `ats cycle validate`, Basis (perp premium over spot index), CLI added, code:sql (ALTER TABLE signals) (+29 more)

### Community 8 - "Weekly Reflection Skill"
Cohesion: 0.06
Nodes (31): Acceptance criteria — Tier 1, Acceptance criteria — Tier 3 (only required when promoting), `ats data validate`, Binance REST, Binance WebSocket, CLI added, code:bash (uv add sqlalchemy[asyncio] asyncpg alembic), code:text (ats db migrate                       # alembic upgrade head) (+23 more)

### Community 9 - "Community 9"
Cohesion: 0.10
Nodes (20): Agent inventory, Agentic Trading System — Architecture, Anti-goals, Build status (as of 2026-05-26), code:text (┌───────────────────────────────┐), Core principles, Definition of done, LLM model tiering (+12 more)

### Community 10 - "Community 10"
Cohesion: 0.10
Nodes (21): code:bash (docker compose -f ops/docker-compose.yml up -d), code:bash (# One-time), code:bash (docker compose -f ops/docker-compose.yml up -d        # Post), Cross-spec conventions, Cumulative state at each spec, Current progress (2026-05-26), Errors, How to use these specs (+13 more)

### Community 11 - "Community 11"
Cohesion: 0.11
Nodes (10): basis (hypertable), candles (hypertable), Data model, funding_rates, funding_rates_xvenue, heartbeats, liquidations, mark_prices_1m (hypertable) (+2 more)

### Community 12 - "Community 12"
Cohesion: 0.12
Nodes (16): Acceptance criteria, `ats gate check`, `ats gate validate`, code:text (ats gate check [--window 90d]        # evaluate the gate aga), code:block2 (DECISION GATE — 2026-05-13  (replay window 2026-02-12 → 2026), Components, Dependencies on prior specs, Goal (+8 more)

### Community 13 - "Community 13"
Cohesion: 0.10
Nodes (21): Agentic Trading System (ATS), code:bash (docker compose -f ops/docker-compose.yml up -d), code:bash (uv sync), code:bash (cp .env.example .env              # set DATABASE_URL; leave ), code:text (agent-orchestration/), code:bash (uv sync                     # install / update deps), Development commands, How to work a spec (+13 more)

### Community 14 - "Community 14"
Cohesion: 0.06
Nodes (36): basis(), candles(), funding(), media(), oi(), _parse_since(), _print_series(), Print a labeled, trend-colored sparkline with first/last/min/max stats. (+28 more)

### Community 15 - "Community 15"
Cohesion: 0.22
Nodes (11): Architecture Document, MCP Server, README Document, Specs Overview, Phase 1: Data Collection, Phase 2: Data Processing, Phase 3: Orchestration, Phase 4: Deep Analysis (+3 more)

### Community 16 - "Community 16"
Cohesion: 0.10
Nodes (23): Programmatic smoke test: backfill 7d BTCUSDT, check row counts, report., validate(), backfill(), media_pull(), _parse_since(), Parse strings like '7d', '120d', '2h', '30m' into timedelta., REST kline + funding + OI + premiumIndex backfill (Tier 1)., One-shot cross-venue funding pull from Bybit + OKX + Hyperliquid (Tier 1). (+15 more)

### Community 17 - "Community 17"
Cohesion: 0.16
Nodes (17): fetch_funding(), fetch_klines(), fetch_open_interest(), fetch_premium_index(), _get(), _ms_to_dt(), Return (data, used_weight). Sleeps briefly if approaching weight limit., Page through /fapi/v1/klines and return all closed bars. (+9 more)

### Community 18 - "Community 18"
Cohesion: 0.25
Nodes (4): main_callback(), ATS CLI entrypoint.  Phase 0: --version, --help. Spec 01 adds: ats db, ats inges, No-op root callback; phase commands are registered here., configure_logging()

### Community 25 - "Community 25"
Cohesion: 0.22
Nodes (15): _align_8h(), fetch_bybit_funding(), fetch_hyperliquid_funding(), fetch_okx_funding(), pull_all(), Snap a datetime down to the nearest 00/08/16 UTC boundary., Fetch and store cross-venue funding for all symbols. Non-fatal on per-venue erro, _upsert_xvenue() (+7 more)

### Community 26 - "Community 26"
Cohesion: 0.12
Nodes (15): AI / agentic trading — *the methodological foil*, code:text (High rigor (deterministic, replayable, gated)), Commercial signal bots — *the output-shape competitors*, Competitive Landscape, Derivatives-data tools — *the input-layer overlap*, Head-to-head comparison, Market map — the four peer groups, Open items to verify (live) (+7 more)

### Community 27 - "Community 27"
Cohesion: 0.24
Nodes (11): check_freshness(), compute_status(), get_data_class_counts(), Row count backing each freshness data_class, for the status table., _ago(), Test freshness status computation., test_funding_budget(), test_missing_beyond_2x() (+3 more)

### Community 28 - "Community 28"
Cohesion: 0.22
Nodes (10): _now_ms(), run(), _since_ms(), upsert_heartbeat(), _entry_id(), _load_feeds(), _parse_published(), pull_all() (+2 more)

### Community 29 - "Community 29"
Cohesion: 0.30
Nodes (11): Base, Basis, Candle, FundingRate, FundingRateXVenue, Heartbeat, Liquidation, MarkPrice1m (+3 more)

### Community 30 - "Community 30"
Cohesion: 0.25
Nodes (7): indexPrice, interestRate, lastFundingRate, markPrice, nextFundingTime, symbol, time

### Community 31 - "Community 31"
Cohesion: 0.47
Nodes (5): _parse_kline(), Verify taker_buy_vol is correctly mapped from kline field index 9., Replicate the mapping logic from binance_rest.upsert_candles., test_taker_buy_vol_matches_field_9(), test_taker_buy_vol_within_volume()

### Community 32 - "Community 32"
Cohesion: 0.40
Nodes (4): downgrade(), migrate(), Apply all pending Alembic migrations (alembic upgrade head)., Downgrade to a specific Alembic revision.

### Community 33 - "Community 33"
Cohesion: 0.60
Nodes (4): _compute_premium_index(), Test basis (premium_index) computation from premiumIndex fixture., test_premium_index_computation(), test_premium_index_within_sane_range()

## Knowledge Gaps
- **261 isolated node(s):** `list`, `symbol`, `markPrice`, `indexPrice`, `lastFundingRate` (+256 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **14 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `validate()` connect `Community 16` to `Community 14`?**
  _High betweenness centrality (0.005) - this node is a cross-community bridge._
- **Why does `Spec 01 — Data Collection · Milestone M1` connect `Weekly Reflection Skill` to `Community 11`?**
  _High betweenness centrality (0.005) - this node is a cross-community bridge._
- **What connects `Mark price downsampling tests — skipped in M1 (Tier 3 / M4 only).`, `Test cross-venue funding parsing — alignment to 8h UTC boundaries.`, `WS parsing tests — skipped in M1 (Tier 3 / M4 only).` to the rest of the system?**
  _306 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `CLI Entrypoint Internals` be split into smaller, more focused modules?**
  _Cohesion score 0.058823529411764705 - nodes in this community are weakly interconnected._
- **Should `Pipeline Phases 1-5` be split into smaller, more focused modules?**
  _Cohesion score 0.06060606060606061 - nodes in this community are weakly interconnected._
- **Should `Package + Analysis Skills` be split into smaller, more focused modules?**
  _Cohesion score 0.06451612903225806 - nodes in this community are weakly interconnected._
- **Should `Top-Level Docs` be split into smaller, more focused modules?**
  _Cohesion score 0.05128205128205128 - nodes in this community are weakly interconnected._