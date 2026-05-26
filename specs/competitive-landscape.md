# Competitive Landscape

**Status:** strategic product review · **Companion:** `architecture.md` (why), `specs/00-roadmap.md` (what) · **Lens:** where ATS wins, where it is exposed, and what the roadmap should defend

> This is a *strategic positioning* review, not a market-sizing or investment
> doc. It maps ATS against four adjacent peer groups to sharpen the build:
> which edges are real and defensible, which are commodity, and what specs
> 01–10 should protect, accelerate, or drop. Competitor facts are qualitative
> and current as of the 2025 landscape; anything pricing- or metric-specific is
> flagged **[verify]** rather than asserted.

---

## What ATS is (one paragraph)

A deterministic signal generator for Binance USDT-margined perpetuals. It
backfills market data + **cross-venue derivatives data** (funding from Bybit /
OKX / Hyperliquid, perp-vs-spot basis) over a handful of majors, normalizes it,
screens for high-attention coins, and runs eight narrow deterministic agents +
an arithmetic synthesizer into a paper signal (entry / stop / target /
confidence / reasons). A replay harness measures edge over 90 days of history,
and a numeric **decision gate** says go/no-go before any LLM, learning loop, or
always-on infrastructure is built. It **does not execute trades**, reads many
venues but trades none, runs at **$0 idle**, and is **CLI/agent-drivable**.

---

## Market map — the four peer groups

ATS doesn't have a single direct competitor; it sits at the intersection of
four adjacent categories, borrowing from each without being any of them.

| Peer group | Representative names | What they sell | Overlap with ATS | The gap ATS fills |
|---|---|---|---|---|
| **OSS algo frameworks** | Freqtrade, Jesse, NautilusTrader, Hummingbot, OctoBot | A toolkit to build & run your own bot | Closest technical cousin: Python, backtest/replay, self-hosted, $0 license | They optimize on history and *execute*; ATS *measures edge then gates*, paper-only, and ships a reasoned multi-agent signal rather than a strategy slot |
| **Commercial signal bots** | 3Commas, Cryptohopper, Coinrule, Pionex, TradeSanta | Hosted SaaS automation + marketplace signals | Same end-user job (a directional crypto signal) | Black-box / copy-trade economics, recurring fees, execution-first; ATS is auditable, free to run, validation-first |
| **Derivatives-data tools** | Coinglass, Laevitas, Velo, Amberdata | Dashboards of funding / OI / liquidations | ATS *ingests the same raw feeds* (cross-venue funding, basis, OI) | They render data for a human to interpret; ATS turns the same feeds into a scored, directional signal with an explicit thesis per agent |
| **AI / agentic trading** | "AI trading agent" frameworks, LLM-strategy projects, on-chain agent tokens | An LLM that "decides" trades | Shares the "agent" vocabulary | They put the LLM *in the decision path* (non-reproducible, confidence-inflated); ATS keeps the signal deterministic and clamps the LLM to ±0.20, never letting it set direction |

The one-line read: **ATS is an OSS-framework-class tool in distribution, a
derivatives-data tool in raw inputs, a signal bot in output shape, and the
*opposite* of the AI-agent crowd in methodology.**

---

## Positioning: rigor × cost

X-axis = **idle cost / ops burden** (left = $0 idle, self-hosted, session-mode;
right = paid SaaS or always-on daemons). Y-axis = **edge-validation rigor**
(bottom = vibes / marketplace signals / "the AI decided"; top = deterministic,
replayable, gated on a numeric go/no-go).

```text
                         High rigor (deterministic, replayable, gated)
                                          |
        Freqtrade / Jesse  ●              |              ● NautilusTrader
        (backtest-tunable,                |                (institutional quant,
         self-hosted)         ★ ATS       |                 paid infra/data)
                          (deterministic  |
                           + replay gate  |
                           + $0 idle)      |
   -------------------------------------------------+-------------------------------------------------
   Low cost / $0 idle                               |                       Paid SaaS / always-on
                                          |
        OctoBot / community ●             |              ● Coinglass / Laevitas / Velo
        signal scripts                    |                (data dashboards, paid tiers)
                                          |
        AI-agent hype /     ●             |              ● 3Commas / Cryptohopper / Coinrule
        on-chain "trader" tokens          |                (hosted bots, copy-trade, subs)
                                          |
                         Low rigor (vibes / black-box / marketplace signals)
```

Two things this picture makes obvious:

1. **The top-left quadrant is nearly empty.** High validation rigor *and* $0
   idle cost is a deliberate, uncrowded position. Freqtrade is the only close
   neighbour, and it lives a notch lower on rigor (it tunes on history — a thing
   ATS explicitly treats as an anti-goal — and has no equivalent of a one-shot
   go/no-go gate).
2. **Almost everyone monetizing is in the bottom-right.** Subscriptions and
   always-on infra correlate with *lower* methodological rigor, because the
   business model rewards engagement and signal volume, not honest edge
   measurement. ATS's "prove edge, then stop if there's none" gate is
   commercially unnatural — which is exactly why incumbents don't do it.

---

## Peer group profiles

### OSS algo frameworks — *the technical cousins*
**Freqtrade, Jesse, NautilusTrader, Hummingbot, OctoBot**

- **What they do:** self-hosted Python/Rust toolkits to define a strategy,
  backtest it, and run it live against exchange APIs.
- **Strengths:** mature, free, large communities, real execution, extensive
  exchange coverage; NautilusTrader brings near-institutional event-driven rigor.
- **Weaknesses:** the user *is* the edge — they hand you a backtester and a
  strategy slot, not a thesis. Backtest-overfit is the default failure mode.
  Hummingbot skews market-making; Freqtrade skews retail TA bots.
- **vs ATS:** ATS borrows their replay/self-host DNA but refuses their core
  loop (optimize-on-history-then-execute). ATS ships an *opinionated* eight-agent
  signal and a *measure-don't-tune* harness, and stops at paper.

### Commercial signal bots — *the output-shape competitors*
**3Commas, Cryptohopper, Coinrule, Pionex, TradeSanta**

- **What they do:** hosted SaaS — drag-and-drop or marketplace strategies, copy
  trading, DCA/grid bots, direct execution on connected exchanges.
- **Strengths:** zero setup, polished UX, large user bases, instant execution,
  marketplaces with social proof.
- **Weaknesses:** black-box signals, recurring fees, incentives tilted to volume
  over edge, recurrent trust/marketplace-quality issues, custodial-key risk.
- **vs ATS:** same end product (a directional call) but the inverse philosophy —
  ATS is auditable to the number, costs nothing to run, and won't ship a signal
  it can't defend on replay. ATS gives up the polished GUI and instant execution.

### Derivatives-data tools — *the input-layer overlap*
**Coinglass, Laevitas, Velo, Amberdata**

- **What they do:** aggregate and visualize funding, open interest,
  liquidations, basis, options skew across venues.
- **Strengths:** broad venue coverage, real-time, the de-facto dashboards for
  derivatives traders; Amberdata/Laevitas reach institutional depth.
- **Weaknesses:** they stop at *display* — interpretation is left to the human.
  Distinctive cross-venue insight is locked behind paid tiers **[verify]**.
- **vs ATS:** ATS ingests the *same* feeds its CrossVenueFlow / Funding / Basis
  agents run on, but converts them into a scored directional thesis rather than a
  chart. The data isn't proprietary — the *deterministic interpretation* of it is
  ATS's contribution. This is also ATS's most imitable surface (see "exposed").

### AI / agentic trading — *the methodological foil*
**LLM-strategy projects, "AI trading agent" frameworks, on-chain agent tokens**

- **What they do:** put an LLM in the decision loop — prompt a model with market
  data and let it pick trades, often wrapped in heavy "autonomous agent" framing.
- **Strengths:** narrative/marketing appeal, fast to prototype, genuinely useful
  for *unstructured* inputs (news, sentiment).
- **Weaknesses:** non-reproducible decisions, confidence inflation ("LLMs always
  agree"), cost per call, no honest backtest (you can't replay a stochastic
  decision), frequent token-hype with no measured edge.
- **vs ATS:** this is the group ATS most deliberately is *not*. ATS's "What the
  LLM is NOT for" section is a direct rebuttal: LLM never touches ingestion or the
  synthesizer, never sets direction, and is hard-clamped to ±0.20. ATS keeps the
  LLM where it's actually good (narrating reasons, classifying media, adjudicating
  genuine ambiguity) and nowhere else.

---

## Head-to-head comparison

Legend: ●●● strong / ●●○ partial / ●○○ weak-or-absent. Rated from the ATS
operator's perspective on the dimensions ATS's architecture cares about.

| Dimension | **ATS** | OSS frameworks | Commercial bots | Derivatives-data | AI-agent tools |
|---|---|---|---|---|---|
| **Edge validated before scale** (gate) | ●●● numeric go/no-go gate | ●●○ backtest (overfit-prone) | ●○○ marketplace stats | ●○○ n/a (data only) | ●○○ rarely measured |
| **Deterministic / auditable signal** | ●●● fully, by design | ●●○ depends on strategy | ●○○ black box | ●●● (raw data) | ●○○ stochastic |
| **Cross-venue derivatives edge** | ●●● core agent | ●○○ DIY | ●○○ | ●●● display only | ●○○ |
| **$0 idle / cost model** | ●●● session-mode, free | ●●● self-host | ●○○ subscription | ●○○ paid tiers | ●○○ per-call cost |
| **Executes trades** | ●○○ paper-only (by choice) | ●●● live | ●●● live | ●○○ no | ●●○ varies |
| **Polished GUI / onboarding** | ●○○ CLI-first | ●●○ varies | ●●● strong | ●●● strong | ●●○ |
| **Learning loop / memory** | ●●○ M2 (specs 07–08) | ●○○ | ●○○ | ●○○ | ●●○ claimed |
| **Agent/CLI drivability** | ●●● MCP + CLI parity (M3) | ●●○ scriptable | ●○○ | ●○○ APIs | ●●○ |
| **Reasoned thesis per signal** | ●●● 8 agents + reasons[] | ●○○ | ●○○ | ●○○ | ●●○ unverifiable |

---

## Where ATS wins

1. **The empty top-left quadrant.** High validation rigor at $0 idle cost is a
   genuinely uncontested position. The business-model gravity that pulls
   competitors toward subscriptions and engagement pulls them *away* from this
   corner.
2. **Cross-venue funding as a signal, not a chart.** Coinglass shows you the
   divergence; ATS scores it and assigns direction. The median retail trader
   doesn't pull Bybit/OKX/Hyperliquid funding at all — and the data tools that do
   stop at display.
3. **The decision gate as a discipline.** Almost no competitor has an explicit
   "stop and admit there's no edge" mechanism. It's commercially unnatural and
   methodologically honest — the single most differentiated idea in the design.
4. **Determinism + replayability.** Because the signal is arithmetic, it can be
   replayed exactly, ablated agent-by-agent, and trusted. The AI-agent cohort
   structurally cannot make this claim.
5. **Methodological credibility vs. the hype cohort.** As "AI trading agents"
   proliferate and mostly disappoint, a system that *explicitly refuses* to put
   the LLM in the decision path is a trust signal to a sophisticated operator.
6. **Portability / agent-drivability.** CLI-first + read-only MCP makes ATS
   drivable by any coding agent — aligned with where tooling is heading and a
   poor fit for GUI-locked SaaS incumbents.

---

## Where ATS is exposed

1. **The data edge is imitable, not proprietary.** Cross-venue funding is public
   REST. The moat is the *deterministic interpretation*, not the feed — a
   well-resourced data tool (Coinglass, Velo) could add a "signal" view. **The
   defensibility has to come from the validated edge + learning loop, not the
   data access.** This is the most important strategic risk.
2. **No execution = a permanent ceiling for many users.** Paper-only is correct
   for validation but means ATS will never be the whole product for someone who
   wants automation. It's a signal *input*, not a trading *system*.
3. **The edge might not exist.** This is the gate's whole point, but it's still
   the dominant risk: if spec 06 returns no-go, the elegant infra is worthless.
   Honesty about this is a strength; the underlying risk is real.
4. **Single-operator, single-venue, ~5→50 symbols.** Deliberately narrow. Fine
   for validation; a hard scaling story later. Competitors span hundreds of pairs
   and dozens of venues out of the box.
5. **CLI-first onboarding excludes the mass market.** A deliberate trade for the
   target user (a technical, skeptical operator), but it forecloses the retail
   SaaS audience entirely.
6. **"Agent" naming collision.** ATS uses "agents" to mean deterministic scoring
   functions — the opposite of the LLM-agent crowd. In a noisy market this risks
   being mistaken for the very thing it rejects. Messaging should lead with
   *deterministic* and *gated*, not *agentic*.

---

## Strategic implications for the roadmap

The landscape sharpens what specs 01–10 should **defend, accelerate, or drop**.

| Implication | Affected specs | Action |
|---|---|---|
| **The gate is the brand.** It's the most differentiated idea and the hardest for incumbents to copy (it fights their business model). | 05, 06 | **Defend / lead with it.** Treat the decision gate and replay harness as the headline, not plumbing. Keep the anti-tuning rule sacred — the moment ATS tunes on history it collapses into the Freqtrade quadrant. |
| **Cross-venue interpretation is the durable data edge — the feed is not.** | 04 (CrossVenueFlow, Funding, Basis) | **Accelerate the interpretation, not the data breadth.** Invest in making the cross-venue *thesis* sharper and ablation-proven (spec 05 matrix). Don't chase more venues for coverage's sake — that's the data tools' game, and you'd lose it. |
| **The learning loop is the only compounding moat.** Determinism is copyable; accumulated, validated learnings are not. | 08 (esp. 8b pgvector) | **Protect its sequencing.** It's correctly gated behind M1, but it's also the long-term defensibility story. Don't under-invest once the gate is green. |
| **Drivability is a real, on-trend differentiator vs. GUI-locked SaaS.** | 09 (MCP + CLI parity) | **Keep, but keep it M3.** Resist pulling it earlier — per the architecture it carries zero edge on its own. Its value is as a *finishing* differentiator once the signal is proven. |
| **Messaging risk: the "agent" collision.** | docs / README | **Reframe externally.** Lead positioning with "deterministic, replay-validated, $0-idle" and treat "agentic" as internal vocabulary. The market's AI-agent fatigue is an opportunity *only* if ATS is clearly distinguished from it. |
| **No-execution ceiling is real but on-strategy.** | scope constraints | **Hold the line, don't drift.** Execution is permanently out of scope — correctly. The strategic answer is to be the *best signal layer that feeds* the OSS frameworks/bots, not to become one. A future "export signal to Freqtrade/webhook" bridge would be the natural, scope-respecting extension. |
| **Always-on tiers are where competitors live — and where cost discipline matters most.** | 10 (M4 Tier 2/3) | **Keep opt-in.** The $0-idle default is a positioning asset, not just a cost choice. Promoting to daemons should stay gated behind proven edge + budget, exactly as specified. |

**Bottom line:** ATS's defensible position is the **top-left quadrant nobody
occupies** — rigor without cost. The two things that keep it there are the
**decision gate** (don't soften it, don't tune on history) and the **learning
loop** (the only edge that compounds). The data feeds and determinism are
necessary but copyable; the discipline and the accumulated learnings are the
moat. Everything in the roadmap that protects those two should be treated as
load-bearing; everything else is, by the architecture's own admission,
infrastructure.

---

## Open items to verify (live)

The strategic read above is robust to these, but if this doc is ever used
externally, confirm the **[verify]** points with current sources:

- Current pricing tiers and what cross-venue insight is free vs. paid on
  Coinglass / Laevitas / Velo.
- Freqtrade / NautilusTrader current feature set re: any built-in walk-forward
  or out-of-sample validation that would narrow the "rigor" gap.
- Whether any commercial bot has shipped a credible *measured-edge* gate (none
  known as of the 2025 landscape).
