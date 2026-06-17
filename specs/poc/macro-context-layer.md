# Spec: Macro Context & Real-Time Data Layer (POC)

**Status:** Draft
**Companion specs:** `specs/poc/idea.md`, `specs/01-data-collection.md`, `specs/07-llm-layer.md`

---

## Objective

Two parallel enhancements to the data stream:

1. **Macro context** — enrich the LLM planning envelope with global crypto market signals (USDT.D, TOTAL3, BTC dominance, sentiment) so `create_plan` reasons about macro regime, not just per-symbol features.

2. **Multi-exchange real-time data** — replace the current Binance-only CVD with a multi-exchange aggregate (8 venues) using `ccxt`, and adopt ccxt as the preferred library for real-time OHLCV, OI, and funding ingestion.

---

## What Is Missing Today

The LLM envelope built in `src/ats/planning/create_plan.py` contains only symbol-local data:

```
features      ← 30+ per-symbol indicators (RSI, EMA, MACD, funding, CVD, OI, etc.)
regime        ← BTC-only trend×vol 2D cell ("bull-low", "bear-high", etc.)
recent_ohlcv  ← last 50 candles (Binance only)
portfolio     ← paper equity + open positions
risk_limits   ← max_position_pct, min_rr
```

**Missing macro signals:**
| Signal | Why It Matters |
|---|---|
| USDT.D (USDT Dominance %) | Rising = risk-off, capital fleeing to stablecoins |
| TOTAL3 (alt market cap ex-BTC/ETH) | Altcoin sector health; divergence from BTC = rotation signal |
| BTC Dominance % | Capital concentration; low and falling = alt season |
| Market-wide sentiment score | Net emotional state of crypto social + news |
| Multi-exchange CVD | Single-exchange CVD misses large OTC and spot flows |

---

## Part 1: Sentiment Integration (external sentiment microservice)

### What the Tool Does

Multi-source sentiment API, 4-hour batch cadence:

| Source | Method | Current Weight | Recommended Weight |
|---|---|---|---|
| CNN Fear & Greed Index | HTTP, 0–100 → [-1,1] | 20% | **40%** |
| NewsData.io | API + NLTK VADER | 20% | **30%** |
| RSS feeds | feedparser + NLTK VADER | 40% | **20%** |
| Reddit (5 subreddits) | PRAW + NLTK VADER | 20% | **10%** |

**Why reweight:**
- Fear & Greed is a composite index already built from 7 factors — it earns the top weight.
- RSS headlines analyzed with VADER are the noisiest signal: VADER can't distinguish "BTC crash feared" (bearish event) from "crash predictions wrong" (net positive). Reducing to 20% limits its damage.
- Reddit sentiment via VADER is laggiest and most contaminated by meme language ("wagmi", "rekt", "to the moon"). 10% keeps it in play without letting it dominate.
- NewsData with keyword-filtered structured API is cleaner than raw RSS scrape; 30% is fair.

**Known remaining weakness:** VADER is not crypto-tuned. Future improvement: replace VADER with FinBERT or a Claude classifier. This spec uses the existing pipeline with corrected weights.

### Output Schema (unchanged)

```json
GET /v1/sentiment/latest →
{
  "final_score": -0.20,
  "label": "Fear",
  "timestamp": "2026-06-03T10:00:00Z",
  "sources": {
    "fear_greed": {"FearAndGreed": 0.45},
    "reddit": {"Cryptocurrency": 0.12, ...},
    "rss": {"CoinTelegraph": 0.02, ...},
    "newsdata": {"crypto": 0.22}
  },
  "cached": true
}
```

### Readiness Assessment

| Criterion | Status | Notes |
|---|---|---|
| REST API | ✅ Ready | FastAPI port 8000, `/v1/sentiment/latest` |
| Output normalized to [-1,1] | ✅ Ready | Matches trade-with-me's percentile rank convention |
| 4h cadence | ✅ Compatible | Aligns with `plan_refresh_bars=16` × 15m = 4h planning cycle |
| Per-source breakdown | ✅ Useful | Can independently weight per-source in envelope |
| Persistent DB | ❌ Missing | Google Sheets only; no PostgreSQL |
| Per-coin sentiment | ❌ Missing | All crypto lumped; no per-symbol breakdown |
| USDT.D / TOTAL3 | ❌ Out of scope | Not this tool's job |

### Integration Path

Use as **read-only external microservice**. Treat as a black-box HTTP data source.

```python
# src/ats/ingestion/macro.py (new)
async def fetch_sentiment(client: httpx.AsyncClient) -> dict | None:
    """Poll sentiment-analysis service. Returns None on failure (graceful degrade)."""
    try:
        resp = await client.get(
            f"{settings.sentiment_svc_url}/v1/sentiment/latest",
            timeout=5.0,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None
```

Result stored in `macro_indicators` table (see DB schema below) and injected into `build_envelope()` as `macro_context.sentiment_score`.

---

## Part 2: Multi-Exchange Real-Time Data (ccxt-based market microstructure layer)

### What the Tool Does

Fetches real-time market microstructure via `ccxt` (no API keys required for public endpoints):

| Signal | Coverage | Output |
|---|---|---|
| CVD | 8 exchanges: Binance, OKX, Bybit, Coinbase, Bitfinex, Kraken, Bitstamp, Crypto.com | Net delta, direction, trend, per-exchange breakdown |
| OI | Any exchange | Current amount/value, trend (rising/falling/flat), 1h history |
| Funding Rates | Any exchange | Current rate %, annualized %, sentiment, period stats |
| OHLCV | Any exchange | Candlestick data, any timeframe, configurable lookback |

### Why This Matters vs. Current Pipeline

| Signal | trade-with-me today | With ccxt |
|---|---|---|
| CVD | Binance klines only (`cvd_30`, `cvd_slope_10`) | **8-exchange aggregate** — catches large OTC flows invisible to Binance alone |
| OI | Binance futures REST | Same (Binance already covered); ccxt abstracts exchange switching |
| Funding | 4-venue xvenue (Binance, Bybit, OKX, HL) | ccxt covers same exchanges; no net gain here |
| OHLCV | Binance REST via `binance_rest.py` | Multi-exchange; useful for price confirmation across venues |

**Primary win: multi-exchange CVD.** Aggregating 8 venues gives a truer picture of whether buyers or sellers are in control — particularly during events where large spot flows go through non-Binance venues.

### Compliance Gap: Synchronous CLI Scripts

The screener's scripts are **synchronous**, file-writing CLI tools. trade-with-me uses `async/await` throughout. Direct import would block the event loop.

**Required refactor:** Extract ccxt fetch logic into async functions using `asyncio.to_thread()`:

```python
# src/ats/ingestion/ccxt_market_data.py (new, adapted from screener)
import asyncio
import ccxt

async def fetch_multi_exchange_cvd(
    symbol: str,
    exchanges: list[str],
    hours: float = 4.0,
    tf: str = "5m",
) -> dict:
    """Aggregate spot CVD across multiple exchanges."""
    return await asyncio.to_thread(_fetch_cvd_sync, symbol, exchanges, hours, tf)

async def fetch_oi(symbol: str, exchange: str = "binance") -> dict:
    """Fetch open interest history. Returns current + trend + hourly history."""
    return await asyncio.to_thread(_fetch_oi_sync, symbol, exchange)

def to_ccxt_symbol(symbol: str) -> str:
    """BTCUSDT → BTC/USDT (assumes USDT pair)."""
    return f"{symbol[:-4]}/USDT"
```

### Integration with Feature Pipeline

Multi-exchange CVD replaces the current Binance-only CVD columns in `features`:

| Current Column | Source | Replacement |
|---|---|---|
| `cvd_30` | Binance klines only | `ccxt_cvd_net_delta` (8-exchange aggregate) |
| `cvd_slope_10` | Computed from above | `ccxt_cvd_trend` (rising/falling/flat classification) |

The existing `percentile_rank()` in `src/ats/processing/normalize.py` applies unchanged to the new CVD columns.

### Universe Screener: Gated on System Stability

A coin screener (CMC + on-chain Dune signals) for dynamic universe selection beyond the hardcoded 5 M1 symbols is **deferred until the core loop (macro context + multi-exchange CVD) is proven stable.** Not part of this spec.

---

## Part 3: Macro Market Data (USDT.D, TOTAL3, BTC Dominance)

Neither external tool provides these. Sources:

| Signal | Source | API Key | Existing Code |
|---|---|---|---|
| BTC Dominance % | CoinGecko `/api/v3/global` | No | None |
| TOTAL3 proxy (`total_mcap - BTC - ETH`) | CoinGecko `/api/v3/global` | No | None |
| USDT Dominance proxy (`usdt_mcap / total_mcap`) | CoinGecko `/api/v3/global` | No | None |
| VIX, DXY, S&P500 | Yahoo Finance | No | `src/ats/ingestion/yahoo.py` (NOT wired) |

**CoinGecko note:** USDT.D is not a direct field — compute as `usdt_mcap / total_mcap`. TOTAL3 = `total_mcap - btc_mcap - eth_mcap`. Both derived from one `/global` call.

### New Ingestion Functions

```python
# src/ats/ingestion/macro.py (continued)

async def fetch_coingecko_global(client: httpx.AsyncClient) -> dict:
    resp = await client.get("https://api.coingecko.com/api/v3/global")
    data = resp.json()["data"]
    total_mcap = data["total_market_cap"]["usd"]
    btc_mcap = total_mcap * data["market_cap_percentage"]["btc"] / 100
    eth_mcap = total_mcap * data["market_cap_percentage"]["eth"] / 100
    usdt_pct = data["market_cap_percentage"].get("usdt", 0)
    return {
        "btc_dominance": data["market_cap_percentage"]["btc"],
        "eth_dominance": data["market_cap_percentage"]["eth"],
        "usdt_dominance": usdt_pct,
        "total3_usd_bn": (total_mcap - btc_mcap - eth_mcap) / 1e9,
        "total_mcap_usd_bn": total_mcap / 1e9,
    }

async def backfill_macro_yahoo(session: AsyncSession, since: timedelta) -> None:
    """Wire existing yahoo.py into the backfill pipeline."""
    # yahoo.py already has fetch_recent(ticker, period, interval) — just call it
    tickers = {"^VIX": "vix", "DX-Y.NYB": "dxy", "^GSPC": "sp500"}
    for ticker, name in tickers.items():
        rows = await fetch_recent(ticker, period=f"{since.days}d", interval="1d")
        await upsert_macro_indicators(session, name, rows, source="yahoo")
```

---

## DB Schema Changes (migration `0004_macro_context`)

### New Table: `macro_indicators`

```sql
CREATE TABLE macro_indicators (
    ts       TIMESTAMPTZ  NOT NULL,
    ticker   TEXT         NOT NULL,
    -- values: "btc_dominance", "usdt_dominance", "total3_usd_bn",
    --         "vix", "dxy", "sp500", "sentiment_score"
    source   TEXT         NOT NULL DEFAULT 'coingecko',
    -- values: "coingecko" | "yahoo" | "sentiment_svc"
    close    NUMERIC      NOT NULL,
    metadata JSONB        NOT NULL DEFAULT '{}',
    PRIMARY KEY (ts, ticker)
);
SELECT create_hypertable('macro_indicators', 'ts');
CREATE INDEX ON macro_indicators (ticker, ts DESC);
```

### Extend `regimes` Table

```sql
ALTER TABLE regimes ADD COLUMN macro_snapshot JSONB DEFAULT '{}';
```

`macro_snapshot` is populated at regime compute time by joining the latest `macro_indicators` rows. Example value:

```json
{
  "btc_dominance": 54.2,
  "usdt_dominance": 5.1,
  "total3_usd_bn": 890.4,
  "vix": 18.5,
  "dxy": 103.1,
  "sp500_close": 5420.0,
  "sentiment_score": -0.20,
  "sentiment_label": "Fear"
}
```

---

## LLM Envelope Extension

`build_envelope()` in `src/ats/planning/create_plan.py` gains a new top-level key:

```python
regime = await state.latest_regime(session, before_ts=as_of)
macro_ctx = (regime.get("macro_snapshot") or {}) if regime else {}

return {
    ...existing keys...
    "macro_context": macro_ctx,
}
```

`src/ats/llm/prompts.py` `PLAN_SYSTEM_PROMPT` should be updated to instruct the model to use `macro_context` for bias — e.g., rising USDT.D or "Extreme Fear" sentiment should bias toward no-trade or defensive setups regardless of per-symbol chart structure.

---

## Data Flow

```
CoinGecko /global (5-15min poll)
  → fetch_coingecko_global()
  → macro_indicators (btc_dominance, usdt_dominance, total3)

Yahoo Finance (daily, on backfill)
  → backfill_macro_yahoo()    ← wraps existing yahoo.py
  → macro_indicators (vix, dxy, sp500)

sentiment-analysis :8000 (4h poll, matches plan_refresh_bars cadence)
  → fetch_sentiment()
  → macro_indicators (sentiment_score)

ccxt (per-bar, live mode only)
  → fetch_multi_exchange_cvd()
  → features table (ccxt_cvd_net_delta, ccxt_cvd_trend)

All of the above ↓

compute_regime()  [src/ats/processing/regime.py]
  → regimes.macro_snapshot = latest macro_indicators joined

build_envelope()  [src/ats/planning/create_plan.py]
  → "macro_context": regime.macro_snapshot
  → "features": includes ccxt CVD columns

create_plan (LLM)
  → sees USDT.D, TOTAL3, BTC dominance, sentiment + enriched CVD
```

---

## Open Questions

1. **ccxt CVD during replay:** Multi-exchange CVD is real-time only — historical trades cannot be aggregated retroactively. `ats engine replay` will continue using Binance-only CVD. Do not compare replay win-rates to live win-rates directly.

2. **CoinGecko rate limits:** Free tier ~10-30 req/min. 5-min poll cadence is safe. Sub-minute would hit limits.

3. **Sentiment service deployment:** Assumes `sentiment-analysis` FastAPI is reachable at `settings.sentiment_svc_url`. Add to `ops/docker-compose.yml` as a service. For local dev, must be started manually.

4. **Graceful degradation:** If any macro source is down, `macro_context` in the envelope should be `{}` (empty) — do not block `create_plan`. The LLM falls back to symbol-local reasoning silently.

---

## Summary Verdict

| Component | Action | Effort |
|---|---|---|
| External sentiment microservice | HTTP poll every 4h; store in `macro_indicators` | Low |
| Sentiment weights | Reweight: FG 40% / NewsData 30% / RSS 20% / Reddit 10% | Low |
| ccxt multi-exchange CVD | Refactor sync→async; replace Binance CVD in feature pipeline | Medium |
| ccxt OHLCV/OI | Optional multi-exchange price confirmation | Low (ccxt already in for CVD) |
| Universe screener | **Deferred** — gated on system stability | — |
| Live order execution | **Out of scope** — paper trading only | — |
| CoinGecko global endpoint | New `fetch_coingecko_global()` | Low |
| Yahoo Finance | Wire existing `yahoo.py` into backfill | Low |
| DB migration `0004` | `macro_indicators` hypertable + `regimes.macro_snapshot` | Low |
| LLM envelope | Add `macro_context` key | Low |
| LLM prompt | Instruct model to use macro context for bias | Low |
