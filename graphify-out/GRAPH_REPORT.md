# Graph Report - agent-orchestration  (2026-05-31)

## Corpus Check
- 77 files · ~50,911 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 945 nodes · 1064 edges · 84 communities (69 shown, 15 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 101 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `07d7ad0d`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
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
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 62|Community 62]]
- [[_COMMUNITY_Community 63|Community 63]]
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 65|Community 65]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 71|Community 71]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 73|Community 73]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 75|Community 75]]
- [[_COMMUNITY_Community 76|Community 76]]
- [[_COMMUNITY_Community 77|Community 77]]
- [[_COMMUNITY_Community 78|Community 78]]
- [[_COMMUNITY_Community 79|Community 79]]
- [[_COMMUNITY_Community 80|Community 80]]
- [[_COMMUNITY_Community 81|Community 81]]
- [[_COMMUNITY_Community 82|Community 82]]
- [[_COMMUNITY_Community 83|Community 83]]

## God Nodes (most connected - your core abstractions)
1. `compute_features_frame()` - 19 edges
2. `Agentic Trading System — Architecture` - 18 edges
3. `Spec 07 — LLM Layer · Milestone M2` - 15 edges
4. `Spec 03 — Orchestration · Milestone M1` - 14 edges
5. `Spec 09 — Interface: UI, MCP & Skills · Milestone M3` - 14 edges
6. `compute_regime()` - 13 edges
7. `Base` - 13 edges
8. `Agentic Trading System (ATS)` - 13 edges
9. `Spec 05 — Replay Harness · Milestone M1` - 13 edges
10. `Spec 04 — Deterministic Signal · Milestone M1` - 13 edges

## Surprising Connections (you probably didn't know these)
- `test_rsi70_boundary()` --calls--> `momentum_composite()`  [INFERRED]
  tests/test_composite.py → src/ats/processing/composite.py
- `test_align_8h_snaps_correctly()` --calls--> `_align_8h()`  [INFERRED]
  tests/test_xvenue_funding_parse.py → src/ats/ingestion/xvenue_funding.py
- `test_align_8h_already_aligned()` --calls--> `_align_8h()`  [INFERRED]
  tests/test_xvenue_funding_parse.py → src/ats/ingestion/xvenue_funding.py
- `test_btcusdt_okx_mapping()` --calls--> `load_xvenue_mapping()`  [INFERRED]
  tests/test_xvenue_symbol_mapping.py → src/ats/ingestion/universe.py
- `test_btcusdt_hyperliquid_mapping()` --calls--> `load_xvenue_mapping()`  [INFERRED]
  tests/test_xvenue_symbol_mapping.py → src/ats/ingestion/universe.py

## Communities (84 total, 15 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.06
Nodes (31): Acceptance criteria, `ats screen validate`, Attention scoring, CLI added, code:bash (uv add 'redis[hiredis]' arq), code:text (ats session run                         # Tier 1 master comm), code:bash (# Phase 1+2 must be running and have ≥7d of features), code:block12 (cycle 2026-05-12T14:00Z   regime=bull-low) (+23 more)

### Community 1 - "Community 1"
Cohesion: 0.06
Nodes (32): Acceptance criteria, API endpoints (read-only), `ats serve validate` / `ats skills validate` / `ats mcp validate`, Body sections (fixed order, so prompt extraction is mechanical), CLI added, CLI parity test pattern, code:bash (uv add fastapi 'uvicorn[standard]'), code:bash (mkdir ui && cd ui) (+24 more)

### Community 2 - "Community 2"
Cohesion: 0.06
Nodes (28): Acceptance criteria, Algorithms, `ats process validate`, CLI added, code:bash (uv add pandas numpy pandas-ta), code:python (def percentile_rank(series: pd.Series, lookback: int) -> pd.), code:python (def momentum_composite(rsi: float, macd_hist: float, roc_5: ), code:text (ats process run                        # one-shot: compute f) (+20 more)

### Community 3 - "Community 3"
Cohesion: 0.05
Nodes (36): Acceptance criteria, Agent specifics (the M2 additions), `ats llm validate`, CLI added, code:bash (uv add anthropic                          # Claude SDK with ), code:bash (uv run ats analyze BTCUSDT       # now shows llm_δ column, s), code:sql (ALTER TABLE agent_runs), code:sql (ALTER TABLE paper_trades ADD COLUMN narrative_ids UUID[] NOT) (+28 more)

### Community 4 - "Community 4"
Cohesion: 0.07
Nodes (26): Acceptance criteria — 8a, Acceptance criteria — 8b (when built), `ats reflect validate`, CLI added, code:bash (uv add pgvector                  # Python client; SQL extens), code:block4 (SETUP), code:python (def retrieve_relevant_learnings(setup_snapshot, k=3, min_con), code:text (# 8a) (+18 more)

### Community 5 - "Community 5"
Cohesion: 0.07
Nodes (25): Acceptance criteria, `ats replay validate`, CLI added, code:block2 (ats replay run --since 90d:), code:block3 (Replay window: 2026-02-12 → 2026-05-13  (90d, ~5 symbols)), code:block4 (data/replay_2026-02-12_2026-05-13/), code:block5 (ats replay run --since 90d --ablate           # baseline + 8), code:block6 (ABLATION MATRIX (90d, ~5 symbols, baseline = all 8 agents)) (+17 more)

### Community 6 - "Community 6"
Cohesion: 0.07
Nodes (26): Acceptance criteria, `ats ops validate`, CLI added, code:bash (uv add 'redis[hiredis]' arq), code:yaml (redis:), code:cron (*/15 * * * *  cd /path/to/ats && uv run ats session run >> l), code:bash (docker compose -f ops/docker-compose.yml up -d        # Post), code:text (ats ingest start                     # WS + continuous polle) (+18 more)

### Community 7 - "Community 7"
Cohesion: 0.05
Nodes (37): Acceptance criteria, Agent contract, agent_runs (per-agent audit trail), Agent specifics (deterministic cores only), `ats cycle validate`, Basis (perp premium over spot index), CLI added, code:sql (ALTER TABLE signals) (+29 more)

### Community 8 - "Community 8"
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
Cohesion: 0.31
Nodes (7): Map a numeric series to Unicode block characters (oldest→newest, left→right)., _sparkline(), Tests for the data-viz sparkline helper and Tier-3 freshness handling., test_sparkline_constant_series_is_lowest_block(), test_sparkline_empty(), test_sparkline_endpoints(), test_sparkline_monotonic_maps_full_range()

### Community 15 - "Community 15"
Cohesion: 0.22
Nodes (11): Architecture Document, MCP Server, README Document, Specs Overview, Phase 1: Data Collection, Phase 2: Data Processing, Phase 3: Orchestration, Phase 4: Deep Analysis (+3 more)

### Community 16 - "Community 16"
Cohesion: 0.10
Nodes (24): Programmatic smoke test: backfill 7d BTCUSDT, check row counts, report., Programmatic smoke test: backfill 7d BTCUSDT, check row counts, report., validate(), backfill(), media_pull(), _parse_since(), Parse strings like '7d', '120d', '2h', '30m' into timedelta., REST kline + funding + OI + premiumIndex backfill (Tier 1). (+16 more)

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
Cohesion: 0.11
Nodes (21): _now_ms(), run(), _since_ms(), check_freshness(), compute_status(), get_data_class_counts(), Row count backing each freshness data_class, for the status table., upsert_heartbeat() (+13 more)

### Community 28 - "Community 28"
Cohesion: 0.12
Nodes (24): compute_regime(), Regime detection — pure compute + DB upsert., Compute regime from BTC 1h candles DataFrame.      btc_1h_df: DataFrame with col, Upsert one regimes row. ON CONFLICT (ts) DO UPDATE., upsert_regime(), _make_btc_1h(), Tests for regime detection pure compute., compute_regime raises ValueError with fewer than 31 bars. (+16 more)

### Community 29 - "Community 29"
Cohesion: 0.26
Nodes (13): Base, Basis, Candle, Feature, FundingRate, FundingRateXVenue, Heartbeat, Liquidation (+5 more)

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

### Community 51 - "Community 51"
Cohesion: 0.12
Nodes (22): basis_premium_from_rows(), basis_z_from_series(), get_basis_for_bar(), Perp basis premium — causal lookup + z-score., Pure core: most recent premium_index with row_ts <= ts.      basis_rows: list of, Rolling 30d z-score of basis premium.      lookback default: 30d × 288 bars/day, DB wrapper: fetch most recent basis_premium ≤ open_time and its z-score.      Re, _dt() (+14 more)

### Community 52 - "Community 52"
Cohesion: 0.11
Nodes (22): funding_divergence_at(), funding_divergence_z(), get_divergence_for_bar(), Cross-venue funding divergence — pure core + DB wrapper., Pure core: compute divergence and peer count.      peer_rates should contain rat, Rolling 30d z-score of funding divergence over 8h boundaries (~90 samples)., DB wrapper: fetch rates at last 8h boundary ≤ open_time, compute divergence., Tests for cross-venue funding divergence pure core. (+14 more)

### Community 53 - "Community 53"
Cohesion: 0.13
Nodes (17): momentum_composite(), Composite momentum indicator., Blend RSI, MACD histogram and ROC into a [0, 1] composite momentum score.      r, Tests for momentum_composite with pinned MACD_SCALE golden values., MACD_SCALE must be pinned at 1.0 (golden value freeze)., rsi=30 → rsi_n = 0.0; with macd_hist=0 → macd_n=0.5; roc_5=0 → roc_n=0.5.      r, macd_hist=0 → macd_n=0.5 (tanh(0)=0, so 0.5+0.5*0=0.5)., Very high RSI, strong positive MACD hist, positive ROC → near 1.0. (+9 more)

### Community 54 - "Community 54"
Cohesion: 0.13
Nodes (19): atr(), obv(), Average True Range (pandas-ta)., Relative Strength Index (pandas-ta)., rsi(), df200(), _make_df(), Tests for pure indicator functions on synthetic 200-bar series. (+11 more)

### Community 55 - "Community 55"
Cohesion: 0.12
Nodes (15): AI Crypto Trader Architecture Summary, code:text (AI decides WHAT to trade), code:json ({), code:text (price > ema_50_5m), code:text (Market Stream (WebSocket)), code:text (create_plan     = strategist), code:text (LLM defines the trading plan.), code:json ({) (+7 more)

### Community 56 - "Community 56"
Cohesion: 0.22
Nodes (13): cvd(), Cumulative Volume Delta and 10-bar OLS slope.      CVD formula: cumsum(2 * taker, _make_candles(), Tests for CVD (Cumulative Volume Delta) indicator., Create a synthetic candle DataFrame with optional taker_buy_vol overrides., cvd_30[i] == cvd_30[i-1] + (2*tbv[i] - vol[i]) for all non-null bars., cvd_slope_10 should be positive for steadily increasing CVD., If any taker_buy_vol in the trailing 30-bar window is NULL, CVD must be NULL. (+5 more)

### Community 57 - "Community 57"
Cohesion: 0.22
Nodes (13): backfill(), compute_features_frame(), _fetch_candles(), Feature orchestrator — compute + upsert features rows., Rolling OLS slope of close price over last `window` bars (closed='left')., Upsert feature rows. ON CONFLICT (symbol, timeframe, open_time) DO UPDATE SET .., Pure: compute all features for a (symbol, tf) candle DataFrame.      candles_df, Compute features for the most recent closed bar for each symbol/timeframe. (+5 more)

### Community 58 - "Community 58"
Cohesion: 0.18
Nodes (12): percentile_rank(), Rolling percentile-rank normalization., Rolling percentile rank over the previous `lookback` closed bars.      Output ∈, Tests for rolling percentile rank normalization., Uniform [0, 100] input → ranks should be approximately linear., rolling(closed='left').rank() places bar i-1's rank at position i.      The resu, Output must be in [0, 1]., Constant series → ranks are undefined (NaN or all equal). (+4 more)

### Community 59 - "Community 59"
Cohesion: 0.14
Nodes (14): ema(), macd(), MACDResult, Pure indicator functions — DataFrame in, Series (or dataclass) out. No DB., Rolling OLS slope using numpy.polyfit over `window` bars (closed='left')., MACD (12/26/9). Returns MACDResult with .macd, .signal, .hist Series., Exponential Moving Average., Annualized realized volatility: rolling std of log returns * sqrt(252*bars_per_d (+6 more)

### Community 60 - "Community 60"
Cohesion: 0.23
Nodes (11): _make_candles(), Tests that compute_features_frame is deterministic (same input → identical frame, Create a synthetic candle DataFrame for feature tests., Same candles input → byte-identical output DataFrame., Output has same number of rows as input and expected columns., All pr_* columns that are non-NaN must be in [0, 1]., Inject an extreme spike at bar 100; verify it doesn't affect bars before 100., test_compute_features_is_deterministic() (+3 more)

### Community 61 - "Community 61"
Cohesion: 0.22
Nodes (9): 1. create_plan, 2. confirm_setup, code:json ({), code:json ({), code:json ({), code:json ({), code:json ({), code:json ({) (+1 more)

### Community 62 - "Community 62"
Cohesion: 0.25
Nodes (8): code:text (Spread increasing), code:text (Price below level for 60 seconds), code:text (5m candle closes below support), code:text (Disable plan), Hard Invalidation, Invalidation Levels, Soft Invalidation, Warning

### Community 63 - "Community 63"
Cohesion: 0.29
Nodes (7): Better Approach, code:python (if price < 67200:), code:text (Warning:), code:json ({), Example, Important Insight, Plan Invalidation

### Community 64 - "Community 64"
Cohesion: 0.40
Nodes (5): _parse_since(), CLI commands for regime inspection., Parse strings like '7d', '120d', '2h', '30m' into timedelta., Show current regime + recent history., show()

### Community 65 - "Community 65"
Cohesion: 0.40
Nodes (5): code:json ({), code:json ({), Hard Rules, Hard Rules vs Soft Rules, Soft Rules

### Community 66 - "Community 66"
Cohesion: 0.40
Nodes (5): code:text (Python), code:text (Python), Minimal Version, Recommended Tech Stack, Scaling Version

### Community 68 - "Community 68"
Cohesion: 0.67
Nodes (3): code:text ("RSI recovers above 45"), code:json ({), Rule Structure

### Community 69 - "Community 69"
Cohesion: 0.67
Nodes (3): code:text (Redis), code:text (active_plan), Shared State

### Community 70 - "Community 70"
Cohesion: 0.67
Nodes (3): code:json ({), code:python (if setup.plan_id != current_plan.plan_id:), Plan Versioning

### Community 71 - "Community 71"
Cohesion: 0.67
Nodes (3): code:json ({), code:python (if now > expires_at:), Setup Expiration

### Community 72 - "Community 72"
Cohesion: 0.67
Nodes (3): code:text (Every tick), code:text (LLM creates invalidation rules), Why Not Use LLM for Invalidation?

### Community 73 - "Community 73"
Cohesion: 0.67
Nodes (3): code:text (create_plan), code:text (confirm_setup), Cost Optimization Strategy

### Community 74 - "Community 74"
Cohesion: 0.10
Nodes (24): backfill(), _f(), _parse_since(), CLI commands for feature processing., Inspect computed features: coverage summary, latest row, and sparklines.      Re, [Tier 3 / M4] Subscribe to candle-close events; compute as bars close., Parse strings like '7d', '120d', '2h', '30m' into timedelta., Programmatic smoke test: assert candles ≥30d, run backfill 7d, check pr_* bounds (+16 more)

### Community 75 - "Community 75"
Cohesion: 0.67
Nodes (3): Rate of Change (pandas-ta)., roc(), test_roc_shape()

### Community 77 - "Community 77"
Cohesion: 0.20
Nodes (9): media(), Run a 'most-recent-first' select of a single numeric column, return oldest→newes, Show heartbeat statuses + row counts. Exits 1 only if a data class is missing., Show heartbeat statuses + row counts. Exits 1 only if a data class is missing., List recent media headlines without opening the DB., List recent media headlines without opening the DB., Run a 'most-recent-first' select of a single numeric column, return oldest→newes, _recent_values() (+1 more)

### Community 78 - "Community 78"
Cohesion: 0.22
Nodes (9): basis(), funding(), _print_series(), Print a labeled, trend-colored sparkline with first/last/min/max stats., Sparkline of recent Binance funding rates; --xvenue compares peers., Sparkline of recent Binance funding rates; --xvenue compares peers., Sparkline of recent basis (premium_index = (mark - index)/index)., Sparkline of recent basis (premium_index = (mark - index)/index). (+1 more)

### Community 79 - "Community 79"
Cohesion: 0.25
Nodes (6): [Exploratory] Fetch Yahoo Finance bars via yfinance. No DB write — feasibility p, [Exploratory] Fetch Yahoo Finance bars via yfinance. No DB write — feasibility p, yahoo(), fetch_recent(), Yahoo Finance — exploratory feasibility probe (NOT wired into M1 ingestion).  `y, Fetch recent OHLCV bars for a Yahoo ticker. Runs the sync client off-thread.

### Community 80 - "Community 80"
Cohesion: 0.33
Nodes (6): candles(), Print a single candlestick chart scaled to `height` terminal rows., Print a single candlestick chart scaled to `height` terminal rows., Show the most recent candle rows for a symbol/timeframe., Show the most recent candle rows for a symbol/timeframe., _render_candle()

### Community 81 - "Community 81"
Cohesion: 0.33
Nodes (6): _parse_since(), Show backfill coverage: row counts, time ranges, and latest values per symbol/tf, Show backfill coverage: row counts, time ranges, and latest values per symbol/tf, Convert e.g. '7d', '2h', '30m' to a UTC datetime., Convert e.g. '7d', '2h', '30m' to a UTC datetime., summary()

### Community 82 - "Community 82"
Cohesion: 0.67
Nodes (3): oi(), Sparkline of recent open-interest (contracts)., Sparkline of recent open-interest (contracts).

### Community 83 - "Community 83"
Cohesion: 0.67
Nodes (3): One-screen dashboard: price, funding, OI, basis, and recent headlines., One-screen dashboard: price, funding, OI, basis, and recent headlines., show()

## Knowledge Gaps
- **298 isolated node(s):** `list`, `symbol`, `markPrice`, `indexPrice`, `lastFundingRate` (+293 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **15 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `compute_features_frame()` connect `Community 57` to `Community 75`, `Community 53`, `Community 54`, `Community 56`, `Community 58`, `Community 59`, `Community 60`?**
  _High betweenness centrality (0.014) - this node is a cross-community bridge._
- **Why does `compute_regime()` connect `Community 28` to `Community 58`, `Community 59`?**
  _High betweenness centrality (0.007) - this node is a cross-community bridge._
- **Are the 14 inferred relationships involving `compute_features_frame()` (e.g. with `test_compute_features_is_deterministic()` and `test_compute_features_output_shape()`) actually correct?**
  _`compute_features_frame()` has 14 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Tests for momentum_composite with pinned MACD_SCALE golden values.`, `MACD_SCALE must be pinned at 1.0 (golden value freeze).`, `rsi=30 → rsi_n = 0.0; with macd_hist=0 → macd_n=0.5; roc_5=0 → roc_n=0.5.      r` to the rest of the system?**
  _462 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.058823529411764705 - nodes in this community are weakly interconnected._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.06060606060606061 - nodes in this community are weakly interconnected._
- **Should `Community 2` be split into smaller, more focused modules?**
  _Cohesion score 0.06451612903225806 - nodes in this community are weakly interconnected._