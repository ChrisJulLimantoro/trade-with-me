# Spec 04 — Deterministic Signal · Milestone M1

> Four narrow **deterministic** scorers run on the same structured input. The
> synthesizer combines them into a structured signal: direction, entry zone, SL,
> TP, confidence, reasons. **No LLM. No vision. No multi-venue data.** This is
> the entire M1 signal — the thing the decision gate (spec 06) judges.
>
> The LLM-assisted half (sentiment, narrative, ±0.20 hybrid deltas, chart
> vision) is deliberately split out into `specs/07-llm-layer.md` (M2). Do not
> build it until the decision gate is green. Refining the data behind a signal
> that has no edge is wasted money.
>
> **Tier behavior:** all four agent functions are imported and called directly
> by the cycle worker (Tier 1: inside `ats session run`; Tier 3 in M4: queued
> per top-pick). Same function, different caller.

---

## Goal

For each top pick produced by spec 03:

1. Run four deterministic agents against shared inputs
2. Synthesize a structured signal: direction, entry zone, SL, TP, confidence, reasons
3. Apply an alignment penalty (disagreement → lower confidence)
4. Apply regime modulation
5. Enforce a 1:2 minimum risk-reward
6. Promote the signal from `proposed` to `active` or `expired`

The output is a fully traceable signal: every field reduces to numbers from
spec 02's features and spec 01's raw data. No "vibes" anywhere.

---

## Milestone & scope

**Milestone:** M1 — Prove Edge.

**In:**
- Four deterministic agents: Structure, Momentum, Funding, Liquidity
- Synthesizer: direction vote, alignment penalty, regime modulation, SL/TP
  rules, RR floor, confidence threshold, sizing
- `signals` table extension (direction/entry/sl/tp/confidence/…)
- `agent_runs` audit table
- Deterministic `reasons[]` (template-rendered from the signal fields — no LLM)

**Out (→ spec 07, M2):**
- Sentiment agent, Narrative agent (LLM-only)
- LLM ±0.20 hybrid deltas on Structure / Liquidity
- Playwright chart vision
- ccxt multi-venue `MarketMetrics`
- The Anthropic client, `llm_costs`, prompt caching, cost budget

**Out (→ spec 08, M2):**
- Paper-trade journaling, outcome tracking, post-mortems, learnings retrieval
  (spec 05's replay harness journals trades for *validation*; spec 08 owns the
  live learning loop)

**Out (always):**
- Auto-execution

---

## Dependencies on prior specs

- **Spec 03:** `top_picks` and `signals` (proposed status) produced per cycle
- **Spec 02:** `features` and `regimes` populated
- **Spec 01:** `candles`, `funding_rates`, `open_interest` populated

---

## New deps to add

**None.** This spec is pure deterministic Python on top of `pandas` / `numpy`
(already added in spec 02). The first new dependency (`anthropic`) arrives in
spec 07.

---

## Data model

### Extend signals (spec 03 base)

```sql
ALTER TABLE signals
  ADD COLUMN direction       TEXT,                 -- 'long' | 'short'
  ADD COLUMN entry_zone      NUMERIC[],            -- [low, high]
  ADD COLUMN stop_loss       NUMERIC,
  ADD COLUMN take_profit     NUMERIC[],            -- [tp1, tp2]
  ADD COLUMN confidence      NUMERIC,
  ADD COLUMN size_pct        NUMERIC,
  ADD COLUMN reasons         TEXT[],
  ADD COLUMN invalidations   TEXT[],
  ADD COLUMN agent_scores    JSONB,                -- {structure: 0.81, momentum: 0.78, ...}
  ADD COLUMN risk_reward     NUMERIC,
  ADD COLUMN alignment_pen   NUMERIC;              -- 0..0.5
```

### agent_runs (per-agent audit trail)

```sql
CREATE TABLE agent_runs (
  id                    UUID PRIMARY KEY,
  signal_id             UUID REFERENCES signals(id) ON DELETE CASCADE,
  agent_name            TEXT NOT NULL,             -- 'structure' | 'momentum' | 'funding' | 'liquidity'
  deterministic_score   NUMERIC NOT NULL,
  llm_delta             NUMERIC,                   -- always NULL in M1; populated in spec 07
  final_score           NUMERIC NOT NULL,
  direction             TEXT NOT NULL,             -- 'long' | 'short' | 'neutral'
  metadata              JSONB NOT NULL,
  ran_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_agent_runs_signal ON agent_runs (signal_id);
```

> `llm_delta`, `llm_input_hash`, `llm_call_id` columns are added by spec 07. M1
> leaves `llm_delta` NULL and skips the others entirely.

---

## Agent contract

All agents implement the same shape — *no inheritance hierarchy beyond this*.

```python
# src/ats/agents/base.py
@dataclass
class AgentInput:
    symbol: str
    cycle_ts: datetime
    timeframe_primary: str             # default '4h'
    features: dict[str, float]          # latest features row
    recent_ohlcv: pd.DataFrame          # last ~200 bars on multiple TFs
    funding_history: pd.DataFrame
    oi_history: pd.DataFrame
    regime: dict                        # current regime cell + components
    # M2 (spec 07) adds: market_metrics, chart_snapshot, media_items, narratives
    # M2 (spec 08) adds: retrieved_learnings

@dataclass
class AgentScore:
    agent: str
    score: float                        # 0..1
    direction: Literal['long', 'short', 'neutral']
    deterministic_score: float
    llm_delta: float | None             # always None in M1
    metadata: dict

class Agent(Protocol):
    name: ClassVar[str]
    uses_llm: ClassVar[bool]             # all False in M1
    async def run(self, ai: AgentInput) -> AgentScore: ...
```

> Spec 07 extends `AgentScore` with `vision_delta` and flips `uses_llm` /
> `uses_vision` on Structure and Liquidity. The M1 contract is forward-compatible
> by construction — new fields default to `None`.

---

## Agent specifics (deterministic cores only)

### Structure

- Pivots: swing-high / swing-low on 4h, 1h
- S/R zones: cluster of pivots within 1.5×ATR
- Breakout: close beyond N-bar high (N=20) with `vol_zscore > 1.5`
- Direction: `long` if breakout above; `short` if breakdown below; else `neutral`
- M1 score is the deterministic score, full stop. (Spec 07 adds an LLM
  adjudication path *only* when the deterministic score is ambiguous, 0.45–0.65.)

### Momentum

- `momentum_composite` from features + 4h slope of RSI
- Direction: `long` if `rsi_4h > 55` and `macd_hist_4h > 0`; `short` if
  `rsi_4h < 45` and `macd_hist_4h < 0`; else `neutral`

### Funding

- Inputs: `funding_z_30d`, `oi_delta_pct_24h` (from spec 02 features)
- Extreme `funding_z_30d` → fade signal
- High OI buildup + extreme funding = crowded; reduce score in the direction of
  the crowd
- Direction: `short` if `funding_z > +2` (longs crowded); `long` if
  `funding_z < -2`; else `neutral`
- (Spec 07 adds the ccxt `market_metrics.spot_cvd_direction` cross-check.)

### Liquidity

> **M1 note:** the rich liquidity agent needs the `liquidations` table, which is
> a WS-fed `[M4]` data class. In M1 there is no continuous liquidation feed.
> The M1 Liquidity agent runs a **lightweight proxy**: it scores proximity to
> recent swing highs/lows weighted by volume (a stand-in for "where stops
> cluster"), derived purely from `candles`. It is honest about being a proxy in
> its `metadata`. The full liquidation-cluster + heatmap-vision version lands in
> spec 07.
>
> If this proxy proves to carry no signal at the decision gate, that is a
> *useful* result — it tells you the liquidation feed (M4 data) is where the
> effort should go, not the LLM layer.

- Proxy core: nearest volume-weighted swing cluster above and below current
  price; cluster magnitude
- Direction: contributes a magnitude, leans `neutral` unless one side is clearly
  closer and heavier

---

## Synthesizer

```python
def synthesize(scores: list[AgentScore], regime: dict,
               features: dict, recent_ohlcv: pd.DataFrame) -> Signal | None:

    # M1 weights — four agents only. Spec 07 re-weights to include
    # sentiment (0.05) and narrative (0.10) and rescales these.
    weights = {'structure': 0.40, 'momentum': 0.30,
               'funding': 0.20, 'liquidity': 0.10}

    # 1. Direction from weighted majority of non-neutral agents
    direction = direction_vote(scores)
    if direction == 'neutral':
        return None

    # 2. Base confidence = weighted mean of |scores in chosen direction|
    base = weighted_mean(scores, weights, direction)

    # 3. Alignment penalty
    variance = score_variance(scores, direction)
    alignment_pen = min(0.5, 1.5 * variance)
    conf = base * (1 - alignment_pen)

    # 4. Regime modulation
    if regime.cell == 'bear-high' and direction == 'long': conf *= 0.85
    if regime.cell == 'bull-low'  and direction == 'long': conf *= 1.05
    conf = clamp(conf, 0, 1)

    # 5. Direction-specific SL/TP rules
    sl, tp = sl_tp_rules(direction, features, recent_ohlcv, structure_score=...)
    entry = current_close_or_zone()
    rr = abs(tp[0] - entry) / abs(entry - sl)
    if rr < 2.0: return None

    # 6. Confidence threshold
    if conf < 0.60: return None

    # 7. Size by confidence tier
    size_pct = size_for(conf)   # 0.60 -> 2%, 0.70 -> 3%, 0.80 -> 4%, 0.90 -> 5%

    return Signal(direction, entry_zone, sl, tp, conf, size_pct, reasons, invalidations,
                  agent_scores=dict_of_scores, risk_reward=rr, alignment_pen=alignment_pen)
```

`reasons[]` in M1 is **template-rendered** from the finalized signal fields
(e.g. `"breakout above 20-bar high with vol_zscore 2.1"`, `"funding z -2.4 →
shorts crowded, long bias"`). No LLM. Spec 07 replaces the template renderer with
an LLM call that produces prose `reasons[]` *after* the deterministic signal is
finalized — the LLM still cannot change any field.

---

## Components

| Path | Responsibility |
|---|---|
| `src/ats/agents/base.py` | `AgentInput`, `AgentScore`, `Agent` protocol |
| `src/ats/agents/structure.py` | deterministic structure core |
| `src/ats/agents/momentum.py` | deterministic momentum |
| `src/ats/agents/funding.py` | deterministic funding |
| `src/ats/agents/liquidity.py` | deterministic liquidity proxy (M1) |
| `src/ats/synthesis/direction.py` | direction vote |
| `src/ats/synthesis/sl_tp.py` | SL/TP rules |
| `src/ats/synthesis/synthesizer.py` | combines scores → signal |
| `src/ats/synthesis/reasons.py` | M1: template renderer (spec 07 swaps in the LLM call) |
| `src/ats/cli/analyze.py` | `ats analyze <SYMBOL>` |
| `src/ats/cli/cycle.py` | `ats cycle run` |
| `src/ats/cli/agent.py` | `ats agent <name> run` — one entry point per agent |

---

## CLI added

```text
ats analyze <SYMBOL>                                  # run all four agents on the symbol; print breakdown
ats agent <name> run --symbol <SYM> --cycle-ts <TS>   # run a single agent
ats cycle run                                         # screen → analyze top picks → emit/promote signals
ats cycle validate                                    # programmatic smoke test
```

No skill surface in M1 — see `specs/00-roadmap.md` → "Skill & MCP inventory".
The `/analyze-symbol` and `/agent-*` skill wrappers are authored in M3.

---

## Validation

### Smoke test

```bash
uv run ats analyze BTCUSDT
```

Expected:

```
agent        det     final    direction
structure    0.74    0.74     long
momentum     0.68    0.68     long
funding      0.45    0.45     neutral
liquidity    0.57    0.57     long

regime: bull-low
alignment penalty: 0.04
base confidence: 0.71
final confidence: 0.74

direction: long
entry zone: [64320, 64480]
stop loss:  62950   (-2.3% / 1.5x ATR_4h)
take profit: [66100, 67800]    rr = 2.4
```

### Acceptance criteria

- [ ] **Golden setup (long bias):** replay a BTC breakout fixture → structure > 0.7, momentum > 0.6, final direction = long
- [ ] **Golden setup (short/reject):** replay an ETH fakeout fixture → final direction = short OR signal rejected on low confidence
- [ ] **RR floor:** any synthesized candidate with `risk_reward < 2.0` does not transition the signal to `active`
- [ ] **Alignment penalty:** fixture with one outlier agent (variance > 0.05) → final confidence < base by at least 5%
- [ ] **Regime modulation:** `bear-high` reduces long confidence; `bull-low` raises it
- [ ] **Determinism:** identical `AgentInput` produces an identical `AgentScore` and an identical synthesized `Signal` — byte-for-byte
- [ ] **No LLM dependency:** the spec-04 code path imports nothing from `src/ats/llm/`; a CI test asserts `anthropic` is not importable in this layer
- [ ] **agent_runs written:** every analyzed top-pick has exactly four `agent_runs` rows; `llm_delta` is NULL on all of them
- [ ] **reasons[] populated:** every `active` signal has 3–5 template-rendered `reasons[]` entries that cite concrete numbers

### pytest

| File | Asserts |
|---|---|
| `tests/test_structure_det.py` | breakout vs fakeout fixtures → expected det scores |
| `tests/test_momentum.py` | RSI + MACD edge cases |
| `tests/test_funding.py` | extreme z-scores → fade direction |
| `tests/test_liquidity_proxy.py` | known swing distribution → known proxy output |
| `tests/test_synthesizer_align.py` | aligned vs misaligned agent vectors |
| `tests/test_synthesizer_regime.py` | bear-high reduces long confidence |
| `tests/test_synthesizer_rr.py` | RR < 2 → rejected |
| `tests/test_synthesizer_determinism.py` | identical input → byte-identical signal |
| `tests/test_reasons_template.py` | finalized signal → expected reason phrases |

### `ats cycle validate`

1. Snapshot a recent cycle's top picks
2. Run analysis against it
3. Assert every top-pick has four `agent_runs` rows
4. Assert no signal with `risk_reward < 2.0` is `active`
5. Re-run; assert byte-identical output
6. Exits 0 on success

---

## Risks / open questions

- **Liquidity proxy may be noise.** The M1 Liquidity agent is a candle-derived
  stand-in for the real liquidation feed. If the decision gate shows it adds
  nothing, that is a *finding*, not a failure — it points effort at the M4
  liquidation feed rather than the M2 LLM layer.
- **Four-agent weights are a guess.** They are version-controlled constants in
  `src/ats/orchestration/weights.py`. Resist tuning them — the replay harness
  (spec 05) *measures*; it does not authorize tuning. Weight changes wait for
  spec 08's reflection data.
- **Direction voting deadlock.** Agents split or all-neutral → reject. Don't
  force a direction from a coin-flip.
- **Template `reasons[]` can feel thin.** That is acceptable for M1 — they exist
  for auditability, not persuasion. Prose `reasons[]` arrive in spec 07.
