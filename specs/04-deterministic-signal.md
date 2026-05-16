# Spec 04 — Deterministic Signal · Milestone M1

> Eight narrow **deterministic** scorers run on the same structured input. The
> synthesizer combines them into a structured signal: direction, entry zone, SL,
> TP, confidence, reasons. **No LLM. No vision.** Multi-venue data is allowed
> (Bybit / OKX / Hyperliquid funding REST + Binance `premiumIndex`) but only as
> a *signal*: the system still trades only on Binance. This is the entire M1
> signal — the thing the decision gate (spec 06) judges.
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

1. Run **eight** deterministic agents against shared inputs
2. Synthesize a structured signal: direction, entry zone, SL, TP, confidence, reasons
3. Apply an alignment penalty (disagreement → lower confidence)
4. Apply regime modulation
5. **Override the entry zone with the PriceAction FVG zone if PriceAction's score and direction qualify**
6. Enforce a 1:2 minimum risk-reward
7. Promote the signal from `proposed` to `active` or `expired`

The output is a fully traceable signal: every field reduces to numbers from
spec 02's features and spec 01's raw data. No "vibes" anywhere.

---

## Milestone & scope

**Milestone:** M1 — Prove Edge.

**In:**
- Eight deterministic agents: Structure, Momentum, Funding, Liquidity,
  PriceAction (FVG), CrossVenueFlow, Basis, CVD
- Synthesizer: direction vote, alignment penalty, regime modulation, SL/TP
  rules, **FVG entry-zone override**, RR floor, confidence threshold, sizing
- `signals` table extension (direction/entry/sl/tp/confidence/…)
- `agent_runs` audit table
- Deterministic `reasons[]` (template-rendered from the signal fields — no LLM)
- **Per-agent ablation reporting hook** (consumed by spec 05's replay harness)

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
- **Spec 02:** `features` and `regimes` populated, including the new derived
  features (`cvd_30`, `cvd_slope_10`, `funding_divergence`,
  `funding_divergence_z_30d`, `funding_peer_count`, `basis_premium`,
  `basis_z_30d`, `pr_cvd_divergence`)
- **Spec 01:** `candles` (with `taker_buy_vol`), `funding_rates`,
  `funding_rates_xvenue`, `open_interest`, `basis` populated

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

### PriceAction (FVG, Option 2 — score + entry-zone override)

**Thesis.** A 3-candle Fair Value Gap left during an impulse is unfilled imbalance:
the middle candle moves so strongly that candle[i-2].high and candle[i].low (bullish)
or candle[i-2].low and candle[i].high (bearish) don't overlap. Published forex/index
studies show ~60% of FVGs hold (don't get fully mitigated within the same session)
when measured by close. No clean crypto-perp study exists, so this number is a
**prior, not a guarantee** — the replay harness (spec 05) is what tells us if it
holds on Binance perps. When a fresh FVG aligns with the structural direction, two
things happen: (a) confidence rises, (b) the gap edges become a tighter entry zone
than the breakout close, mechanically improving RR because the gap sits closer to
the structural invalidation point.

**Logic (deterministic).**

```text
inputs:  recent_ohlcv (4h primary), atr_4h, current_close, structure_direction
window:  last 5 closed 4h candles

1. detect FVG on each rolling 3-candle window i:
   bullish_fvg if candle[i-2].high < candle[i].low      # gap below current price
   bearish_fvg if candle[i-2].low  > candle[i].high     # gap above current price

2. for the most recent gap within the window:
   gap_size       = |candle[i-2].high - candle[i].low|   (or symmetric for bearish)
   relative_size  = gap_size / atr_4h
   age_bars       = i_now - i_gap

3. direction_alignment_bonus = 1.0  if FVG direction matches structure_direction
                               0.3  otherwise

   score = clamp(relative_size, 0, 1) * direction_alignment_bonus

4. direction = 'long'    if bullish_fvg
              'short'    if bearish_fvg
              'neutral'  if no qualifying gap

5. metadata.fvg_zone (the candidate entry zone for the synthesizer's override):
   bullish: [candle[i-2].high, candle[i].low]
   bearish: [candle[i].high,  candle[i-2].low]

6. metadata.fvg_age_bars = age_bars
```

**Invalidation rule.** If the most recent 4h candle closed *through* the gap
(full close-mitigation), `score = 0`, direction = `neutral`, and no entry-zone
override is produced. Wick mitigation does not invalidate (consistent with the
published "by close" measurement convention).

**Synthesizer integration.** See "Entry-zone override" in the Synthesizer section
below. Briefly: if `score > 0.6` and direction matches the synthesized direction,
the synthesizer replaces the default `current_close ± ATR band` entry zone with
`metadata.fvg_zone`. Otherwise the default applies and PriceAction influences only
confidence.

**M1 deferred (FVG Option 3).** A two-stage `pending → active on retrace` signal
lifecycle is **explicitly not** in M1. It requires changes to spec 03's signal
state machine and reduces the closed-signal sample size that spec 06's gate needs
(many signals would expire without retrace, never reaching `paper_trades`).
Revisit in M2 only if PriceAction earns its weight in the M1 ablation matrix.

---

### CrossVenueFlow (cross-exchange funding divergence)

**Thesis.** Funding rates on Binance, Bybit, OKX, and Hyperliquid settle at the same
8h UTC ticks (00:00 / 08:00 / 16:00). Persistent same-symbol divergence across
venues reflects participant-mix asymmetry — Binance is heavily retail with high
leverage, Hyperliquid skews professional, OKX and Bybit sit between. Documented
cross-venue funding-arb APRs of 5.98–11.4% base with 23%+ peaks confirm the spread
is real and exploitable. The system does **not** execute the arbitrage (multi-venue
execution is permanently out of scope per `architecture.md`); it uses the
divergence as a **directional signal**: the venue with extreme funding hosts the
crowded crowd, and Binance trades are biased *against* that crowd.

**Logic (deterministic).**

```text
inputs:  features.funding_divergence, features.funding_divergence_z_30d,
         features.funding_peer_count  (all computed in spec 02 from
         funding_rates_xvenue)

1. if funding_peer_count < 2:
       score = 0, direction = 'neutral', metadata.reason = 'insufficient_peers'
       return

2. z = features.funding_divergence_z_30d

3. score = min(1.0, |z| / 3.0)

4. direction:
       'short'   if z > +1.5      # Binance funding much higher than peers
                                  # → Binance longs crowded → fade with short
       'long'    if z < -1.5      # Binance funding much lower than peers
                                  # → Binance shorts crowded → fade with long
       'neutral' otherwise

5. metadata = {
       binance_rate:    features.funding_rate,
       divergence:      features.funding_divergence,
       divergence_z:    z,
       peer_count:      features.funding_peer_count,
   }
```

**Robustness.** A single peer is too noisy to anchor a median, so the agent
abstains (`score = 0`) when fewer than 2 peers are available for the cycle. The
spec-01 freshness output makes peer availability visible in `ats data status`.

**No execution implication.** This agent says "Binance longs look crowded" — it
does **not** instruct the system to short Hyperliquid or arbitrage the spread.
The signal influences a Binance-only paper trade, nothing more.

---

### Basis (perp premium over spot index)

**Thesis.** Binance publishes a `premiumIndex` per perp expressing
`(mark - index) / index` — i.e. how far the perp has decoupled from its
spot-aggregate index. When the 30d z-score of premium hits an extreme,
late-arriving longs / shorts have stacked the perp at a premium / discount that
has historically mean-reverted. This is the *signal version* of the spot-perp
basis trade (which itself shows 18% APY base / 31% with prediction when executed
as a delta-neutral arb). As a signal-only input, the edge is weaker than the
arb's, but it's free, REST-only, and orthogonal to candle-based indicators.

**Logic (deterministic).**

```text
inputs:  features.basis_premium, features.basis_z_30d
         (both computed in spec 02 from the `basis` table)

1. if basis_z_30d IS NULL  (basis row missing or warm-up not complete):
       score = 0, direction = 'neutral', metadata.reason = 'no_basis'
       return

2. z = features.basis_z_30d

3. score = min(1.0, |z| / 3.0)

4. direction:
       'short'   if z > +2.0      # perp very rich vs spot → fade
       'long'    if z < -2.0      # perp very cheap vs spot → fade
       'neutral' otherwise

5. metadata = {
       premium_index: features.basis_premium,
       basis_z:       z,
   }
```

**Regime affinity.** This agent is at its best at range / extremes; in strong
trend regimes the basis can stay one-sided for days and the agent will fire
direction-against-trend, which the synthesizer's alignment penalty correctly
penalizes. That is the intended behavior — Basis exists *to disagree* in
extreme conditions, and the synthesizer aggregates the disagreement.

---

### CVD (cumulative volume delta divergence)

**Thesis.** CVD = cumulative `(taker_buy_vol - taker_sell_vol)` per bar. When
price makes a new high but CVD does not (or vice versa), aggressive buying isn't
confirming the move — distribution. Symmetric for new lows. CVD is foundational
in crypto perp order-flow (Cryptocred's guide, Bookmap, Phemex Academy all treat
it as a core indicator).

**Logic (deterministic).**

```text
prereq: candles.taker_buy_vol exists (added in spec 01)
inputs: features.cvd_30, features.cvd_slope_10, features.pr_cvd_divergence,
        recent_ohlcv (4h primary, last 30 closed bars)

1. price_NH = recent_ohlcv.high.iloc[-1] == recent_ohlcv.high.tail(30).max()
   price_NL = recent_ohlcv.low.iloc[-1]  == recent_ohlcv.low.tail(30).min()

2. cvd_NH = features.cvd_30 == cvd_30_rolling_max(30)
   cvd_NL = features.cvd_30 == cvd_30_rolling_min(30)

3. bearish_div = price_NH and not cvd_NH        # price high, buyers not confirming
   bullish_div = price_NL and not cvd_NL        # price low,  sellers not confirming

4. score = clamp(features.pr_cvd_divergence, 0, 1)  # 0..1 by construction

5. direction:
       'short'   if bearish_div
       'long'    if bullish_div
       'neutral' otherwise

6. metadata = {
       cvd_slope_10:  features.cvd_slope_10,
       price_slope:   recent_ohlcv.close.diff().tail(10).mean(),
       divergence_type: 'bearish' | 'bullish' | 'none',
   }
```

**Null handling.** If `cvd_30` is NULL for the current bar (any of the last 30
bars had NULL `taker_buy_vol`), the agent abstains.

---

## Synthesizer

```python
def synthesize(scores: dict[str, AgentScore], regime: dict,
               features: dict, recent_ohlcv: pd.DataFrame) -> Signal | None:

    # M1 weights — eight agents. Hand-set, version-controlled in
    # src/ats/orchestration/weights.py. Anti-tuning rule applies:
    # weights stay fixed across replay runs.
    # Spec 07 re-weights to include sentiment (0.05) and narrative (0.10)
    # and rescales these.
    weights = {
        'structure':     0.25,
        'momentum':      0.15,
        'funding':       0.10,
        'liquidity':     0.05,
        'price_action':  0.15,    # FVG (new in M1)
        'cross_venue':   0.15,    # cross-exchange funding divergence (new)
        'basis':         0.10,    # perp-vs-spot premium z-score (new)
        'cvd':           0.05,    # cumulative volume delta divergence (new)
    }   # sum = 1.00

    # Agents that abstain (score=0, direction=neutral) are still passed in;
    # the weighted_mean / direction_vote treat their score as 0 and ignore
    # their direction vote.

    # 1. Direction from weighted majority of non-neutral agents
    direction = direction_vote(scores, weights)
    if direction == 'neutral':
        return None

    # 2. Base confidence = weighted mean of |scores in chosen direction|
    base = weighted_mean(scores, weights, direction)

    # 3. Alignment penalty (8 agents → variance is more informative;
    #    threshold tuned for 8-vector but the formula is unchanged)
    variance = score_variance(scores, direction)
    alignment_pen = min(0.5, 1.5 * variance)
    conf = base * (1 - alignment_pen)

    # 4. Regime modulation (unchanged)
    if regime.cell == 'bear-high' and direction == 'long': conf *= 0.85
    if regime.cell == 'bull-low'  and direction == 'long': conf *= 1.05
    conf = clamp(conf, 0, 1)

    # 5. Direction-specific SL/TP rules
    sl, tp = sl_tp_rules(direction, features, recent_ohlcv,
                         structure_score=scores['structure'].score)

    # 6. Entry-zone override (FVG Option 2) — the only place a single agent
    #    influences signal *shape* rather than confidence
    pa = scores['price_action']
    if pa.score > 0.6 and pa.direction == direction:
        entry_zone = pa.metadata['fvg_zone']      # [low, high]
        entry = (entry_zone[0] + entry_zone[1]) / 2
        reasons.append(f"entry refined to FVG zone {entry_zone}")
    else:
        entry_zone = current_close_band(close, atr_4h)
        entry = close

    rr = abs(tp[0] - entry) / abs(entry - sl)
    if rr < 2.0: return None

    # 7. Confidence threshold
    if conf < 0.60: return None

    # 8. Size by confidence tier
    size_pct = size_for(conf)   # 0.60 -> 2%, 0.70 -> 3%, 0.80 -> 4%, 0.90 -> 5%

    return Signal(direction, entry_zone, sl, tp, conf, size_pct, reasons, invalidations,
                  agent_scores=dict_of_scores, risk_reward=rr, alignment_pen=alignment_pen)
```

**Anti-tuning recap.** Weights live as named constants in
`src/ats/orchestration/weights.py`. Do not search them. The decision gate
(spec 06) may *drop* an agent whose ablation contribution (spec 05) is ≤ 0 —
that is structurally different from retuning weights. After a drop, the
remaining weights are *re-normalized arithmetically* (each weight ÷ sum of
remaining weights), never re-discovered.

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
| `src/ats/agents/price_action.py` | FVG detection + `fvg_zone` metadata (new) |
| `src/ats/agents/cross_venue.py` | cross-exchange funding divergence (new) |
| `src/ats/agents/basis.py` | perp-vs-spot premium z-score (new) |
| `src/ats/agents/cvd.py` | cumulative volume delta divergence (new) |
| `src/ats/orchestration/weights.py` | hand-set agent weights (8 entries) + re-normalization helper for ablation-dropped agents |
| `src/ats/synthesis/direction.py` | direction vote |
| `src/ats/synthesis/sl_tp.py` | SL/TP rules |
| `src/ats/synthesis/synthesizer.py` | combines scores → signal; applies FVG entry-zone override |
| `src/ats/synthesis/reasons.py` | M1: template renderer (spec 07 swaps in the LLM call) |
| `src/ats/cli/analyze.py` | `ats analyze <SYMBOL>` |
| `src/ats/cli/cycle.py` | `ats cycle run` |
| `src/ats/cli/agent.py` | `ats agent <name> run` — one entry point per agent (now 8) |

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
agent           det     final    direction    notes
structure       0.74    0.74     long
momentum        0.68    0.68     long
funding         0.45    0.45     neutral
liquidity       0.57    0.57     long
price_action    0.82    0.82     long         FVG zone [64280, 64360]
cross_venue     0.61    0.61     short        binance funding +z 1.9 vs peers
basis           0.38    0.38     neutral
cvd             0.55    0.55     long         bearish div absent

regime: bull-low
alignment penalty: 0.07     (cross_venue disagrees)
base confidence: 0.69
final confidence: 0.67

direction: long
entry zone: [64280, 64360]   (FVG override applied — PriceAction score 0.82 > 0.6)
stop loss:  62950            (-2.1% / 1.5x ATR_4h)
take profit: [66100, 67800]  rr = 2.6
```

Notes on reading the breakdown:

- `cross_venue` voting `short` while everyone else votes `long` is normal —
  CrossVenueFlow is *supposed* to dissent when Binance funding is hot. The
  alignment penalty (0.07) absorbs the disagreement; the synthesizer doesn't
  flip direction unless the weighted majority does.
- `price_action`'s metadata `fvg_zone` triggered the entry-zone override, which
  is the only spec-04 path that lets a single agent change signal *shape*.
- `basis` is neutral here (z within ±2). When it fires direction-against-trend
  in extreme regimes, expect the alignment penalty to absorb it the same way.

### Acceptance criteria

- [ ] **Golden setup (long bias):** replay a BTC breakout fixture → structure > 0.7, momentum > 0.6, final direction = long
- [ ] **Golden setup (short/reject):** replay an ETH fakeout fixture → final direction = short OR signal rejected on low confidence
- [ ] **RR floor:** any synthesized candidate with `risk_reward < 2.0` does not transition the signal to `active`
- [ ] **Alignment penalty:** fixture with one outlier agent (variance > 0.05) → final confidence < base by at least 5%
- [ ] **Regime modulation:** `bear-high` reduces long confidence; `bull-low` raises it
- [ ] **Determinism:** identical `AgentInput` produces an identical `AgentScore` and an identical synthesized `Signal` — byte-for-byte
- [ ] **No LLM dependency:** the spec-04 code path imports nothing from `src/ats/llm/`; a CI test asserts `anthropic` is not importable in this layer
- [ ] **agent_runs written:** every analyzed top-pick has exactly **eight** `agent_runs` rows; `llm_delta` is NULL on all of them
- [ ] **reasons[] populated:** every `active` signal has 3–5 template-rendered `reasons[]` entries that cite concrete numbers
- [ ] **FVG entry-zone override:** on a fixture where PriceAction.score > 0.6 and direction matches synth direction, `signals.entry_zone == PriceAction.metadata.fvg_zone` (not the default close±ATR band); on a fixture where the FVG closed through, the default applies
- [ ] **CrossVenueFlow abstain:** on a fixture with `funding_peer_count < 2`, CrossVenueFlow.score == 0, direction = neutral, and `agent_runs.metadata.reason == 'insufficient_peers'`
- [ ] **Basis abstain:** on a fixture where `basis_z_30d IS NULL`, Basis.score == 0 and the synthesizer does not crash on the missing input
- [ ] **CVD abstain:** on a fixture where any of the last 30 `taker_buy_vol` is NULL, CVD.score == 0 and synthesizer proceeds normally
- [ ] **Weights sum to 1.00:** `sum(weights.values()) == 1.0` at startup; deviation raises at import time
- [ ] **Weight re-normalization:** after dropping an agent (decision-gate ablation result), the remaining weights renormalize to sum to 1.0 — covered by a dedicated test

### pytest

| File | Asserts |
|---|---|
| `tests/test_structure_det.py` | breakout vs fakeout fixtures → expected det scores |
| `tests/test_momentum.py` | RSI + MACD edge cases |
| `tests/test_funding.py` | extreme z-scores → fade direction |
| `tests/test_liquidity_proxy.py` | known swing distribution → known proxy output |
| `tests/test_price_action_fvg.py` | bullish / bearish / no-FVG / mitigated fixtures → expected score, direction, `fvg_zone`, and invalidation behavior |
| `tests/test_cross_venue.py` | peer_count 0/1/2/3, |z| at 1.0/1.5/2.5 → expected score, direction, abstain when peer_count<2 |
| `tests/test_basis.py` | basis_z at NULL / ±1 / ±2.5 → expected score, direction, abstain on NULL |
| `tests/test_cvd_agent.py` | bearish-div / bullish-div / no-div fixtures → expected score, direction; NULL `cvd_30` → abstain |
| `tests/test_synthesizer_align.py` | aligned vs misaligned agent vectors (8-dim) |
| `tests/test_synthesizer_regime.py` | bear-high reduces long confidence |
| `tests/test_synthesizer_rr.py` | RR < 2 → rejected |
| `tests/test_synthesizer_fvg_override.py` | PriceAction.score > 0.6 and direction-matched → `entry_zone == fvg_zone`; score < 0.6 → default band |
| `tests/test_synthesizer_determinism.py` | identical input (8 agents) → byte-identical signal |
| `tests/test_weights_invariants.py` | weights sum to 1.0; dropping an agent → remaining renormalize to 1.0 |
| `tests/test_reasons_template.py` | finalized signal → expected reason phrases (including the FVG override phrase) |

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
- **Eight-agent weights are a guess.** They are version-controlled constants in
  `src/ats/orchestration/weights.py`. Resist tuning them — the replay harness
  (spec 05) *measures*; it does not authorize tuning. Weight changes wait for
  spec 08's reflection data. The decision gate's only authorized action against
  the weight set is to **drop** an agent with ablation contribution ≤ 0 and
  arithmetically renormalize.
- **FVG prior is from forex/index, not crypto perps.** The ~60% hold rate is a
  starting hypothesis only. The replay ablation either confirms PriceAction
  earns its weight on crypto-perp data or it doesn't — the *prior* is not
  evidence on its own.
- **CrossVenueFlow correlation with Funding.** The existing Funding agent and
  CrossVenueFlow both read funding rates. They are not redundant — Funding
  reads Binance funding *level* (extremity), CrossVenueFlow reads Binance
  funding *relative to peers* (asymmetry). A symbol can have high Binance
  funding that's matched by peers (Funding fires, CrossVenueFlow does not) or
  median-Binance funding that's much higher than peers (CrossVenueFlow fires,
  Funding does not). The ablation matrix will reveal if they double-count in
  practice.
- **Basis can be persistently one-sided in trending regimes.** This is the
  intended behavior — Basis is designed to dissent during extreme stretches.
  The synthesizer's alignment penalty correctly handles the dissent.
- **CVD requires the new `taker_buy_vol` column.** A cold-start without
  re-backfill leaves `taker_buy_vol = 0` (the migration default), which makes
  CVD silently always equal `-volume`. The spec-01 acceptance criterion
  "`taker_buy_vol IS NULL OR taker_buy_vol > volume` returns 0" plus the
  spec-02 NULL-handling rule guard against this, but it is easy to break in
  practice. Watch for it.
- **Direction voting deadlock.** Agents split or all-neutral → reject. Don't
  force a direction from a coin-flip.
- **Template `reasons[]` can feel thin.** That is acceptable for M1 — they exist
  for auditability, not persuasion. Prose `reasons[]` arrive in spec 07.
