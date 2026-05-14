# Spec 08 — Learning · Milestone M2

> Every live signal becomes a paper trade. Every closed trade feeds a weekly
> reflection. Once enough trades accumulate, closed trades also become
> retrievable memory that nudges future analysis.
>
> This spec has **two sub-stages**:
>
> - **8a — Reflection (build now).** `paper_trades` for the live session loop,
>   outcome reconciliation, the weekly reflection report. At this stage
>   "self-learning" honestly means *the operator reads the reflection and decides
>   what to change*. That is enough, and it is real.
> - **8b — Memory (build later).** pgvector embeddings of setup fingerprints,
>   LLM post-mortems written as structured `learnings`, and retrieval injected
>   into the spec-07 synthesizer. Build 8b only once 8a has produced enough
>   closed trades that there is something worth embedding (rule of thumb: ≥ 40
>   closed live trades). Embedding three trades teaches nothing.
>
> Auto-weight-tuning is **not** in this spec at all — it needs ≥ 100 closed
> signals and stays a manual decision.

---

## Goal

Close the loop without closing the human out of it.

**8a:**
1. Every `active` signal is journaled as a paper trade (entry, SL, TP, size)
2. Open trades are closed on SL / TP / expiry
3. A weekly reflection report aggregates outcomes by regime × narrative × confidence

**8b (later):**
4. On close, an LLM post-mortem writes a structured `learning` row
5. Learnings are embedded (pgvector) and retrievable in future synthesis
6. The spec-07 synthesizer pulls the top-3 similar learnings as non-binding context

---

## Milestone & scope

**Milestone:** M2 — Sharpen.

**In (8a):**
- The live-session `paper_trades` writer (`source='session'`) — reusing the table
  and `close_paper_trade()` function created in **spec 05**
- Outcome reconciliation for live sessions:
  - **Tier 1:** spec 03's session-start candle-replay walk calls the closer
  - **Tier 3 (M4):** an async mark-price poller — same `close_paper_trade()`
- `pnl_pct`, `max_drawdown_pct`, `max_favorable_pct`, `time_to_exit_minutes`
- Narrative tagging of paper trades (`narrative_ids`, from spec 07)
- Weekly reflection report → `reflections` table + Markdown file

**In (8b — later):**
- LLM post-mortem → Pydantic `Learning` per close
- `learnings` table + setup-fingerprint embedding (pgvector)
- Retrieval injected into the spec-07 synthesizer

**Out:**
- Auto-weight tuning (manual review only, ≥ 100 closed signals)
- Live execution
- New exchange data

---

## Dependencies on prior specs

- **Spec 07:** signals emitted with the full LLM-layer treatment;
  `narratives` populated
- **Spec 05:** the `paper_trades` table and `close_paper_trade()` already exist
  — spec 08 reuses them, it does not recreate them
- **Spec 01:** `mark_prices_1m` populated (only needed for the Tier-3 poller, M4)

---

## New deps to add

**8a:** none.

**8b:**
```bash
uv add pgvector                  # Python client; SQL extension installed in spec 01
uv add sentence-transformers     # local embeddings for the setup fingerprint
```

Embeddings use a local `SentenceTransformer` (`all-mpnet-base-v2`, 768d) — cheap,
fast, no extra API calls.

---

## Data model

### paper_trades — reuse + extend

`paper_trades` was created in **spec 05**. Spec 08 adds nothing structural except
confirming the `source` column distinguishes `'replay'` / `'session'` / `'live'`
and `narrative_ids` (added in spec 07) is populated for session/live trades. The
live writer inserts rows with `source='session'`; the M4 poller uses
`source='live'`.

### learnings (8b)

```sql
CREATE TABLE learnings (
  id                       UUID PRIMARY KEY,
  paper_trade_id           UUID NOT NULL REFERENCES paper_trades(id),
  category                 TEXT NOT NULL,         -- enum below
  hypothesis               TEXT NOT NULL,         -- 1–2 sentences
  evidence                 TEXT NOT NULL,
  proposed_adjustment      TEXT NOT NULL,         -- ≤ 200 chars
  confidence_in_lesson     NUMERIC NOT NULL,      -- 0..1
  setup_embedding          vector(768) NOT NULL,
  embedding_version        INT NOT NULL DEFAULT 1,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_learnings_setup_emb ON learnings
  USING ivfflat (setup_embedding vector_cosine_ops);
CREATE INDEX idx_learnings_category ON learnings (category);
```

**Learning categories** (enum, enforced in Pydantic): `false_breakout`,
`funding_misread`, `regime_shift`, `narrative_reversal`, `liquidity_sweep`,
`alignment_too_low`, `sentiment_noise`, `clean_win`, `clean_loss`, `other`.

### reflections (8a)

```sql
CREATE TABLE reflections (
  id                       UUID PRIMARY KEY,
  period_start             TIMESTAMPTZ NOT NULL,
  period_end               TIMESTAMPTZ NOT NULL,
  hit_rate_overall         NUMERIC NOT NULL,
  sample_size              INT NOT NULL,
  hit_rate_by_regime       JSONB NOT NULL,
  hit_rate_by_confidence   JSONB NOT NULL,
  hit_rate_by_narrative    JSONB NOT NULL,
  hit_rate_by_agent        JSONB NOT NULL,
  markdown                 TEXT NOT NULL,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## Components

| Path | Sub-stage | Responsibility |
|---|---|---|
| `src/ats/journal/writer.py` | 8a | on signal → `active`, insert a `paper_trade` (`source='session'`) |
| `src/ats/journal/snapshot.py` | 8a | builds `setup_snapshot` JSONB at entry |
| `src/ats/journal/outcome_tracker.py` | 8a (Tier-1) / M4 (Tier-3) | Tier-1: thin caller of spec 05's `close_paper_trade()` from the session reconciliation; Tier-3: async mark-price poller |
| `src/ats/reflection/aggregate.py` | 8a | numeric aggregations over the period |
| `src/ats/reflection/report.py` | 8a | weekly: aggregate + optional LLM narrative summary (**Opus** — rare, high-value aggregate reasoning) → `reflections` + `reports/YYYY-MM-DD-weekly.md` |
| `src/ats/learning/embed.py` | 8b | `embed_setup(snapshot) -> vector(768)` (local SentenceTransformer) |
| `src/ats/learning/post_mortem.py` | 8b | LLM call (**Opus** — genuinely hard causal reasoning about why a trade worked or didn't) → Pydantic `Learning`; writes row + embedding |
| `src/ats/learning/retrieval.py` | 8b | `find_similar_learnings(setup_snapshot, k=3)` — used by spec 07 synthesizer |
| `src/ats/learning/correlation.py` | 8b | narrative-outcome correlation each cycle |
| `src/ats/cli/journal.py` | 8a | `ats journal ...` |
| `src/ats/cli/reflect.py` | 8a | `ats reflect ...` |
| `src/ats/cli/learn.py` | 8b | `ats learn ...` |

---

## Setup fingerprint (8b — embedding input)

The text we embed is **structured**, not free-form, so similar setups land
nearby:

```
SETUP
direction: {long|short}
regime_cell: {bull-low|...}
narrative_tags: {comma-sep}
agent_scores:
  structure={0.81} momentum={0.78} funding={0.45} liquidity={0.61}
  sentiment={0.62} narrative={0.55}
features (normalized):
  pr_atr={0.91} pr_vol_zscore={0.78} pr_momentum={0.66} pr_funding={0.31} pr_oi={0.50}
outcome_hint: {pre-filled at retrieval time only}
```

Same template for storage and retrieval. `outcome_hint` is omitted when storing a
*new* learning; at retrieval it biases toward similar *and informative* memories.

---

## Retrieval rules (8b — used by the spec-07 synthesizer)

```python
def retrieve_relevant_learnings(setup_snapshot, k=3, min_conf=0.5, max_tokens=800):
    emb = embed_setup(setup_snapshot)
    rows = (SELECT * FROM learnings
            WHERE confidence_in_lesson >= {min_conf}
            ORDER BY setup_embedding <=> {emb}
            LIMIT {k})
    return truncate_by_tokens(rows, max_tokens)
```

Retrieved learnings are a **non-binding context block** — they cannot change
deterministic outputs, only nudge the LLM's `reasons[]` toward acknowledging
known traps.

---

## Reflection report (8a — weekly)

Aggregations: overall hit rate, per-regime, per-narrative, per-confidence-bucket,
per-agent-agreement; a reliability diagram (confidence bucket × realized hit
rate); average `pnl_pct` of winners vs losers. (Once 8b exists, also: top-5 most
retrieved learnings.)

Output: a `reflections` row + `reports/YYYY-MM-DD-weekly.md` + an optional
LLM-generated narrative summary (≤ 300 words) at the top.

Both LLM call sites in this spec — the post-mortem (8b) and the weekly reflection
summary (8a) — use **Opus** (`claude-opus-4-7`). They are rare (per-close /
weekly) and the reasoning is genuinely hard, so the per-call cost is negligible
and worth it. This is the opposite end of the tiering matrix from spec 07's
high-volume Haiku calls — see `architecture.md` → "LLM model tiering".

**This report is the M2 "learning" in its honest form.** You read it. You decide
whether a regime is underperforming or a confidence bucket is miscalibrated. The
system surfaces; the human adjusts.

---

## CLI added

```text
# 8a
ats journal [--status open|closed]
ats journal show <paper_trade_id>
ats reflect [--since 7d]               # read the latest reflection
ats reflect run [--since 7d]           # rerun the aggregation
ats reflect validate                    # programmatic smoke test

# 8b (later)
ats learn post-mortem <paper_trade_id> # one-shot post-mortem
ats learn retrieval <signal_id>        # show what the synthesizer pulled
```

Skill wrappers (`/post-mortem`, `/weekly-reflection`) are authored in **spec 09
(M3)**.

---

## Validation

### Smoke test (8a — after spec 07 has emitted some live signals)

```bash
uv run ats session run                  # emits + journals signals; reconciles on next run
uv run ats journal --status closed
uv run ats reflect run --since 7d
ls reports/   # → YYYY-MM-DD-weekly.md present
```

### Acceptance criteria — 8a

- [ ] **Journal on activation:** every signal that reaches `active` has exactly one `paper_trades` row with `source='session'`
- [ ] **Outcome correctness:** 10 fixture trades with known price paths → correct `exit_reason` and `pnl_pct` for all 10
- [ ] **Closer reuse:** the session outcome tracker calls spec 05's `close_paper_trade()` — a test asserts there is no second copy of the close logic
- [ ] **Reflection coverage:** with ≥ 4 weeks of paper trades, the reliability diagram has ≥ 10 samples in each confidence bucket
- [ ] **Reflection idempotent:** running `ats reflect run` twice for the same window → one `reflections` row, markdown overwritten
- [ ] **Narrative tagging:** every session paper trade has `narrative_ids` populated from the cycle's active narratives

### Acceptance criteria — 8b (when built)

- [ ] **Threshold respected:** 8b is not started until ≥ 40 closed `source='session'` trades exist
- [ ] **Learning written:** every closed paper trade has exactly one `learnings` row
- [ ] **Embedding shape:** every `setup_embedding` is exactly 768 dimensions
- [ ] **Retrieval finds known similar:** a synthetic learning with a specific fingerprint is in the top-3 for a near-identical signal
- [ ] **No data leakage:** a learning written at time T is not retrievable for a signal generated at T' < T
- [ ] **Token cap:** the synthesizer's learnings context block is ≤ 800 tokens

### pytest

| File | Sub-stage | Asserts |
|---|---|---|
| `tests/test_outcome_tracker.py` | 8a | scripted price feed → expected SL/TP/expiry outcomes |
| `tests/test_journal_writer.py` | 8a | signal → active produces exactly one row |
| `tests/test_reflection_aggregations.py` | 8a | 50 closed trades fixture → expected hit-rate table |
| `tests/test_embedding_shape.py` | 8b | embedding is a 768-d float vector |
| `tests/test_retrieval_nearest.py` | 8b | seeded learnings → expected top-3 |
| `tests/test_retrieval_time_filter.py` | 8b | future learnings excluded |
| `tests/test_postmortem_categories.py` | 8b | invalid category → rejected |

### `ats reflect validate`

1. Asserts ≥ 1 closed paper trade exists
2. Runs `reflection.run()` on the last 30d
3. Asserts a `reflections` row + markdown file written, all JSONB keys present
4. Exits 0 on success

---

## Risks / open questions

- **8b too early is worse than no 8b.** Embedding a handful of trades produces
  confident-sounding noise that then *nudges future signals*. The ≥ 40-trade
  threshold is a floor, not a target — wait for it.
- **Hindsight bias.** The LLM rationalizes every loss in 20/20 vision. Mitigation:
  cap `proposed_adjustment` at 200 chars; weekly manual prune of low-quality
  learnings; watch the `confidence_in_lesson` distribution — always > 0.8 means
  the LLM is overconfident.
- **Embedding scale drift.** If feature semantics change, old embeddings degrade.
  `embedding_version` is on the table; bump it and re-embed when needed.
- **Auto-tuning temptation.** Resist. The first learnings will be surprising; let
  them accumulate before touching weights.
