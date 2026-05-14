# Spec 07 — LLM Layer · Milestone M2

> **Do not start this spec until the decision gate (spec 06) is GO.**
>
> M1 proved a deterministic signal has edge. M2 sharpens it. This spec adds the
> LLM-assisted half that was deliberately split out of M1: two LLM-only agents
> (Sentiment, Narrative), bounded ±0.20 hybrid deltas on Structure and Liquidity,
> optional chart vision, and the ccxt multi-venue `MarketMetrics` input. It also
> **expands the universe to ~50 symbols and turns on the diversity filter** that
> spec 03 deferred.
>
> The LLM never sets direction and never moves confidence by more than ±0.20.
> It refines a signal that already works; it cannot manufacture edge.

---

## Goal

For each top pick:

1. Run the four M1 deterministic agents (spec 04) — unchanged
2. Add: Sentiment (per symbol, LLM), Narrative (per cycle, LLM)
3. Add: bounded LLM/vision deltas on Structure and Liquidity (±0.20, shared budget)
4. Add: ccxt `MarketMetrics` as an input to Funding and Liquidity
5. Re-weight the synthesizer to include sentiment + narrative
6. Replace M1's template `reasons[]` with LLM-generated prose `reasons[]`
7. Expand the universe to ~50 and enable spec 03's diversity filter

---

## Milestone & scope

**Milestone:** M2 — Sharpen.

**In:**
- LLM-only agents: Sentiment (per symbol), Narrative (per cycle, across all media)
- Hybrid agents: Structure and Liquidity gain a bounded LLM adjudication path
  (only when their deterministic score is ambiguous) and an optional vision path
- ccxt `MarketMetrics` (multi-venue OI / CVD / funding crowding)
- Optional Playwright chart vision for Structure / Liquidity
- Anthropic client wrapper: prompt caching, retry, structured-output enforcement
- `llm_costs` table + per-cycle budget guard
- `narratives` + `narrative_items` tables
- Universe expansion to ~50; **enable the diversity filter + `categories.yaml`**
  (the design is already in spec 03, marked "Deferred to M2")
- X/Twitter media ingestion turned on (spec 01 deferred it)

**Out:**
- Paper-trade journaling, outcome tracking, learnings → spec 08
- Auto-execution (always out)
- Re-running the replay harness (spec 05) with the LLM layer is an *M2 activity*,
  not a deliverable of this spec — but you should do it, to confirm M2 beats the
  M1 baseline.

---

## Dependencies on prior specs

- **Spec 06: GO verdict.** This is a hard gate, not a soft suggestion.
- **Spec 04:** the four deterministic agents and the synthesizer
- **Spec 03:** the diversity filter + `categories.yaml` (deferred design)
- **Spec 01:** `media_items` populated (RSS; X now enabled), `liquidations`
  becomes relevant — note its full value needs the M4 WS feed; until then the
  Liquidity LLM path works off the candle proxy + whatever liquidation history
  exists

---

## New deps to add

```bash
uv add anthropic                          # Claude SDK with prompt caching
uv add ccxt                               # multi-venue OI / CVD / funding for MarketMetrics
uv add tweepy                             # X/Twitter ingestion (was deferred in spec 01)
uv add playwright                         # optional, for chart-screenshot vision
uv run playwright install chromium        # one-time, after `playwright` is added
```

`playwright` is **optional**. Without it, Structure and Liquidity skip the vision
path and fall back to JSON-only (no change to their deterministic core). The
vision path is also gated by the per-cycle cost budget. No new infra.

---

## Non-negotiable LLM rules

1. **No raw price text in prompts.** LLMs see only structured JSON. Chart *images*
   (vision) are a separate channel, not a textual leak.
2. **Pydantic-validated output.** Parse failure → fall back to deterministic-only
   score; log the incident.
3. **Hard ±0.20 clamp.** Hybrid agents may modify confidence by at most ±0.20
   from the deterministic score. The LLM cannot flip direction. Vision deltas
   share the same ±0.20 budget — they do not get a second allotment.
4. **Prompt caching ≥ 70% hit rate.** Cache the static prefix (rules + schema +
   context envelope). Vary only the per-call payload.
5. **Cost budget per cycle.** Default $0.50/cycle. Over budget → remaining agents
   short-circuit to deterministic-only.
6. **Audit trail.** Every LLM call writes an `llm_costs` row (input hash, tokens,
   USD cost).
7. **Single-source prompts.** Each agent's system prompt lives in
   `.claude/skills/agent-<name>/SKILL.md` under a `## System prompt` section,
   read by `src/ats/agents/prompts.py` at import. A test asserts byte-identity.
   *Note:* the `SKILL.md` files themselves are **authored in spec 09 (M3)** — in
   M2 the prompts may live in a single `prompts/` directory and migrate to
   `SKILL.md` during M3. The byte-identity contract applies from whichever source
   is canonical at the time.
8. **Model selection per call site.** There is no single "the model." Each call
   site uses the cheapest model that can do its job — the matrix in
   `architecture.md` → "LLM model tiering" is canonical. For M2:
   **Sentiment → Haiku**, **`reasons[]` → Haiku**, **Structure/Liquidity hybrid
   deltas → Sonnet**, **Narrative → Sonnet**. (Post-mortem and weekly reflection
   → Opus, but those land in spec 08.) The model is a per-call argument, never a
   hard-coded constant.

---

## Data model

### Extend signals + agent_runs

```sql
ALTER TABLE agent_runs
  ADD COLUMN llm_input_hash  TEXT,
  ADD COLUMN llm_call_id     UUID REFERENCES llm_costs(id);
-- agent_runs.llm_delta already exists (spec 04); now it gets populated for hybrids
ALTER TABLE agent_runs
  ADD COLUMN vision_delta    NUMERIC;              -- part of the shared ±0.20 budget
```

### narratives + narrative_items

```sql
CREATE TABLE narratives (
  id                    UUID PRIMARY KEY,
  first_seen            TIMESTAMPTZ NOT NULL,
  last_updated          TIMESTAMPTZ NOT NULL,
  status                TEXT NOT NULL,            -- 'active' | 'fading' | 'archived'
  name                  TEXT NOT NULL,
  strength              NUMERIC NOT NULL,         -- 0..1
  momentum              NUMERIC NOT NULL,         -- d/dt strength
  related_symbols       TEXT[] NOT NULL,
  description           TEXT NOT NULL
);

CREATE TABLE narrative_items (
  narrative_id          UUID REFERENCES narratives(id) ON DELETE CASCADE,
  media_item_id         UUID REFERENCES media_items(id) ON DELETE CASCADE,
  relevance             NUMERIC NOT NULL,
  PRIMARY KEY (narrative_id, media_item_id)
);
```

### llm_costs

```sql
CREATE TABLE llm_costs (
  id              UUID PRIMARY KEY,
  cycle_ts        TIMESTAMPTZ NOT NULL,
  caller          TEXT NOT NULL,                  -- 'structure' | 'liquidity' | 'sentiment' | 'narrative' | 'reasons'
  symbol          TEXT,                            -- NULL for cycle-level calls (Narrative)
  model           TEXT NOT NULL,
  tokens_in       INT NOT NULL,
  tokens_out      INT NOT NULL,
  cache_read      INT NOT NULL DEFAULT 0,
  cache_write     INT NOT NULL DEFAULT 0,
  usd_cost        NUMERIC NOT NULL,
  input_hash      TEXT NOT NULL,
  ran_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_llm_costs_cycle ON llm_costs (cycle_ts);
```

### paper_trades.narrative_ids

```sql
ALTER TABLE paper_trades ADD COLUMN narrative_ids UUID[] NOT NULL DEFAULT '{}';
```

---

## Extended agent contract

Spec 04's `AgentInput` / `AgentScore` gain fields. They were designed
forward-compatible — new fields default to `None`.

```python
@dataclass
class MarketMetrics:
    """Position-flow snapshot from ccxt. Computed once per (symbol, cycle_ts),
    cached as data/market_metrics/{symbol}_{cycle_ts}.json."""
    symbol: str
    spot_cvd_4h: float
    spot_cvd_direction: Literal['rising', 'falling', 'flat']
    oi_trend: Literal['rising', 'falling', 'flat']
    oi_change_pct_1h: float
    oi_change_pct_24h: float
    funding_now: float
    funding_z_30d: float
    funding_crowding: Literal['longs_pay_shorts', 'shorts_pay_longs', 'neutral']
    venues_aggregated: list[str]
    captured_at: datetime

@dataclass
class ChartSnapshot:
    path_4h: Path | None
    path_1h: Path | None
    path_15m: Path | None

# AgentInput gains: market_metrics, chart_snapshot, media_items, narratives
# AgentScore gains: vision_delta (shares the ±0.20 budget with llm_delta)
```

---

## Agent specifics (the M2 additions)

### Structure — add LLM + vision · model: Sonnet

LLM adjudication runs **only when the deterministic score is ambiguous**
(0.45–0.65). Input: structured JSON (pivots, ATR, breakout flag, OHLCV summary)
plus m15/h1/h4 chart PNGs if present. Output: `{delta ∈ [-0.2, 0.2],
confidence_in_judgment, observed_patterns}`. Clamp delta; never invert direction.

### Liquidity — add LLM + vision + real liquidation clusters · model: Sonnet

Replaces spec 04's candle proxy with real liquidation-cluster detection
(sliding-window sum within 0.5% price bins over 24h) where liquidation data
exists. LLM adjudication runs only when clusters are present on both sides
within 2×ATR. Input includes the 4h chart PNG (Coinglass liquidation heatmap
visible). Output: `{preferred_sweep_direction, delta ∈ [-0.2, 0.2], reasoning}`.

### Funding — add the MarketMetrics cross-check

Still deterministic, but now cross-checks `market_metrics.spot_cvd_direction`: if
spot CVD opposes the crowded futures position, fade harder.

### Sentiment (LLM only) — new · model: Haiku

Input: last 24h of `media_items` mentioning the symbol. Output:
`{sentiment_score ∈ [-1,1], confidence, key_themes}`. **Sentiment only adjusts
confidence (synthesizer weight 0.05); it never selects direction.**

### Narrative (LLM, cycle-level) — new · model: Sonnet

Input: last 7 days of `media_items`, aggregated. Output: list of active
narratives `{name, strength, momentum, related_symbols, description}`. Stored in
`narratives` + `narrative_items`. Used by the synthesizer for **modulation only**
(±0.05) and by spec 08 to tag paper trades.

---

## Synthesizer changes

```python
# M2 weights — re-weighted from spec 04's four-agent vector
weights = {'structure': 0.30, 'momentum': 0.25, 'funding': 0.15,
           'liquidity': 0.15, 'sentiment': 0.05, 'narrative': 0.10}
```

New modulation step, inserted after regime modulation:

```python
# Narrative modulation
if narrative_aligns(narratives, symbol, direction): conf += 0.05
elif narrative_contradicts(narratives, symbol, direction): conf -= 0.05
```

`reasons[]` is now produced by an LLM (**Haiku** — this is near-templating)
**after** the deterministic signal is finalized — the LLM is told the structured
signal and asked for 3–5 short phrases. **It cannot change any field.** This
replaces spec 04's template renderer in `src/ats/synthesis/reasons.py`.

---

## Universe expansion + diversity filter

- Universe grows from ~5 majors to **top-50 USDT-perp by rolling 24h quote
  volume**, refreshed every 6h (spec 01's universe resolver, previously deferred).
- Spec 03's **diversity filter** and `seeds/categories.yaml` are now enabled
  (`MAX_PER_CATEGORY` default 2). The greedy top-N-with-caps algorithm is already
  specified in spec 03 — this spec just turns it on.
- Re-run spec 05's replay over the expanded universe to get an M2 baseline.

---

## Components

| Path | Responsibility |
|---|---|
| `src/ats/agents/market_metrics.py` | ccxt-driven `MarketMetrics`; writes `data/market_metrics/*.json` |
| `src/ats/agents/charts.py` | Playwright chart capture; no-ops gracefully if Playwright missing |
| `src/ats/agents/prompts.py` | reads the canonical system prompt per agent (see LLM rule 7) |
| `src/ats/agents/sentiment.py` | LLM only |
| `src/ats/agents/narrative.py` | LLM only, cycle-level |
| `src/ats/agents/structure.py` | spec 04 core + LLM/vision path (extends, doesn't replace) |
| `src/ats/agents/liquidity.py` | spec 04 proxy → real clusters + LLM/vision path |
| `src/ats/agents/funding.py` | spec 04 core + MarketMetrics cross-check |
| `src/ats/synthesis/synthesizer.py` | re-weighted; narrative modulation added |
| `src/ats/synthesis/reasons.py` | template renderer → LLM call |
| `src/ats/llm/client.py` | Anthropic wrapper: cache, retry, structured output, vision. Takes `model` as a per-call argument — never a hard-coded constant (see LLM rule 8) |
| `src/ats/llm/budget.py` | per-cycle USD cap; raises `BudgetExceeded` |
| `src/ats/llm/schema.py` | Pydantic models for each LLM output |
| `src/ats/ingestion/x_poller.py` | tweepy poll (enabled now) |
| `seeds/cvd_venues.yaml` | exchanges aggregated for spot CVD |
| `seeds/categories.yaml` | category map for the diversity filter |
| `seeds/x_accounts.yaml` | curated X handles |

---

## CLI added

```text
ats narratives [--active|--all]
ats cycle cost [--cycle <T>]         # show llm_costs for a cycle
# ats analyze / ats agent / ats cycle run gain their LLM paths (same commands, richer output)
ats llm validate                      # programmatic smoke test
```

Skill wrappers (`/analyze-symbol`, `/agent-*`) are still authored in **spec 09
(M3)** — M2 remains CLI-first.

---

## Validation

### Smoke test

```bash
uv run ats analyze BTCUSDT       # now shows llm_δ column, sentiment, narrative
uv run ats cycle cost
```

### Acceptance criteria

- [ ] **Gate precondition:** `ats gate check` from spec 06 returned GO before this spec was started (recorded in `data/gate_<date>.md`)
- [ ] **LLM clamp:** 100 mocked LLM calls returning ±0.5 deltas → every `agent_runs.llm_delta` ∈ [-0.20, 0.20]
- [ ] **Vision shares the budget:** when both `llm_delta` and `vision_delta` are set, `|llm_delta + vision_delta| ≤ 0.20`
- [ ] **No direction flip:** no LLM or vision path ever changes an agent's direction
- [ ] **Parse-fail fallback:** malformed LLM JSON → deterministic-only score; incident logged
- [ ] **Cost guard:** simulated cycle at cumulative $0.51 → next agent uses deterministic-only; warning logged
- [ ] **Prompt cache:** 10 consecutive `ats analyze BTCUSDT` → cache-read tokens > cache-write tokens for ≥ 8/10
- [ ] **Sentiment is not a direction-setter:** a fixture where sentiment is the only non-neutral agent → no signal emitted
- [ ] **Diversity filter on:** 100 replayed cycles over the ~50 universe → no cycle has > 2 symbols per category
- [ ] **M2 beats M1 baseline:** re-running spec 05's replay with the LLM layer produces expectancy ≥ the M1 gate baseline (if it doesn't, the LLM layer is cost without benefit — investigate before continuing)

### pytest

| File | Asserts |
|---|---|
| `tests/test_llm_clamp.py` | LLM returns 0.5 → clamped to 0.2 |
| `tests/test_vision_clamp.py` | llm + vision both +0.15 → final delta +0.20 (shared cap) |
| `tests/test_llm_parse_fail.py` | malformed JSON → det-only fallback |
| `tests/test_budget.py` | over-budget cycle stops calling the LLM |
| `tests/test_vision_missing_playwright.py` | Playwright ImportError → JSON-only, no exception |
| `tests/test_market_metrics.py` | ccxt fixture OI-rising + CVD-falling → expected `funding_crowding` |
| `tests/test_synthesizer_narrative.py` | narrative alignment ±0.05 |
| `tests/test_diversity_filter.py` | top-15 raw → expected top-10 after caps |
| `tests/test_sentiment_not_direction.py` | sentiment-only non-neutral → no signal |

### `ats llm validate`

1. Runs `ats analyze` on a fixture symbol with a mocked Anthropic client
2. Asserts every LLM delta ∈ ±0.20, costs logged, cache headers present
3. Asserts deterministic fallback on a forced parse failure
4. Exits 0 on success

---

## Risks / open questions

- **LLM "always-agree" bias.** LLMs nudge toward the dominant signal, inflating
  confidence. Mitigation: the ±0.20 bound, plus monitor the `llm_delta`
  distribution — if its mean is far from zero, recalibrate prompts.
- **Cost creep.** 50 symbols × LLM agents × 96 cycles/day runs hot. **Model
  tiering is the primary lever** — putting Haiku on the high-volume calls
  (Sentiment, `reasons[]`) is what keeps the $0.50/cycle budget realistic;
  reserve Sonnet for the bounded-judgment calls and never use Opus in the cycle
  path at all (Opus is spec-08-only). Aggressive prompt caching is mandatory on
  top of that; the budget guard is the backstop.
- **The LLM layer might not help.** It is entirely possible the M2 replay shows
  no improvement over M1. That is a real finding — the LLM layer is then cost
  without benefit and should be trimmed, not defended.
- **`categories.yaml` rot.** Coin categories drift; quarterly review is
  non-negotiable. Bad category → broken diversity filter.
- **Liquidation data quality.** The richest Liquidity path needs the M4 WS feed.
  Until then it is partial — set expectations accordingly.
