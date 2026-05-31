# AI Crypto Trader Architecture Summary

## Objective

Build an automated crypto trading system that combines:

- AI reasoning for strategy and trade judgment
- Deterministic code for real-time execution
- Low latency
- Low operating cost
- Scalability across multiple symbols
- Strong risk controls

---

# Core Principle

Separate slow strategic thinking from fast execution.

```text
AI decides WHAT to trade
Code decides WHEN to trade
Risk system decides IF it is safe
```

---

# High-Level Architecture

```text
Market Stream (WebSocket)
        ↓
Feature Builder
        ↓
Shared State (Redis)
        ↓

 ┌─────────────────────┬─────────────────────┐
 ↓                     ↓

Planning Loop          Execution Loop
(create_plan)          (real-time)
 ↓                     ↓
Update Plan        Rule Engine
                        ↓
                  Setup Detected
                        ↓
                  confirm_setup
                        ↓
                  Risk Manager
                        ↓
                  Order Execution
```

---

# Trading Skill Design

Instead of multiple independent agents, use a single Trading Skill with two modes.

## 1. create_plan

Runs periodically (15–60 minutes).

Responsibilities:

- Analyze market structure
- Determine market bias
- Generate trade setups
- Define entry zones
- Define take profit targets
- Define stop loss levels
- Define invalidation logic
- Output structured executable rules

Input:

```json
{
  "market_context": {},
  "portfolio": {},
  "risk_limits": {}
}
```

Output:

```json
{
  "market_bias": "bullish",
  "allowed_setups": [...]
}
```

---

## 2. confirm_setup

Runs only when a setup candidate is detected.

Responsibilities:

- Validate setup quality
- Confirm or reject trade
- Reduce size if necessary
- Ensure market conditions still match plan

Possible outputs:

```json
{
  "action": "CONFIRM"
}
```

```json
{
  "action": "REJECT"
}
```

```json
{
  "action": "WAIT"
}
```

```json
{
  "action": "REDUCE_SIZE"
}
```

---

# Feature Builder

Converts raw exchange data into structured features.

Example:

```json
{
  "price": 67920,
  "rsi_5m": 47.8,
  "ema_50_5m": 67780,
  "spread_bps": 0.8,
  "volume_zscore_5m": 1.4
}
```

The rest of the system consumes these features.

---

# Rule Engine

The Rule Engine is the real-time detector.

It continuously evaluates live features against the active trading plan.

Example:

```json
{
  "left": "price",
  "operator": ">",
  "right": "ema_50_5m"
}
```

The Rule Engine evaluates:

```text
price > ema_50_5m
```

without calling an LLM.

---

# Rule Structure

Natural language should never be used directly for execution.

Bad:

```text
"RSI recovers above 45"
```

Good:

```json
{
  "left": "rsi_5m",
  "operator": ">",
  "right": 45
}
```

---

# Hard Rules vs Soft Rules

## Hard Rules

Must pass.

Example:

```json
{
  "left": "spread_bps",
  "operator": "<",
  "right": 2
}
```

---

## Soft Rules

Contribute confidence.

Example:

```json
{
  "left": "volume_zscore_5m",
  "operator": ">",
  "right": 1.0,
  "weight": 0.3
}
```

Soft rules allow scoring instead of requiring perfection.

---

# Shared State

Components communicate through shared state.

Example:

```text
Redis
```

Stores:

```text
active_plan
latest_features
portfolio_state
risk_state
```

This avoids direct dependencies between services.

---

# Plan Versioning

Every plan must have a unique identifier.

Example:

```json
{
  "plan_id": "btc_plan_2026_05_29_1600"
}
```

Detected setups inherit that plan ID.

Before execution:

```python
if setup.plan_id != current_plan.plan_id:
    reject_trade()
```

This prevents execution based on stale plans.

---

# Setup Expiration

Every setup should have a short lifespan.

Example:

```json
{
  "expires_at": "2026-05-29T15:22:30Z"
}
```

Before execution:

```python
if now > expires_at:
    reject_trade()
```

This prevents late entries.

---

# Plan Invalidation

## Important Insight

Plan invalidation should NOT rely on a single price tick.

Bad:

```python
if price < 67200:
    invalidate_plan()
```

This is too sensitive.

---

## Better Approach

Use:

- buffers
- candle close confirmation
- duration confirmation
- severity levels

Example:

```text
Warning:
Price briefly dips below level

Soft Invalidation:
Price stays below level for 60 seconds

Hard Invalidation:
5-minute candle closes below level
```

---

## Example

```json
{
  "invalidation_rules": [
    {
      "type": "hard",
      "condition": "5m_close_below_67200"
    }
  ]
}
```

---

# Invalidation Levels

## Warning

No action.

Used for monitoring.

Example:

```text
Spread increasing
Volume weakening
```

---

## Soft Invalidation

Pause new entries.

Existing positions remain open.

Example:

```text
Price below level for 60 seconds
```

---

## Hard Invalidation

Kill plan.

Example:

```text
5m candle closes below support
Major market structure break
```

Actions:

```text
Disable plan
Cancel pending setups
Request replan
```

---

# Why Not Use LLM for Invalidation?

Do NOT do:

```text
Every tick
→ Ask LLM if plan is invalid
```

Problems:

- expensive
- slow
- inconsistent
- difficult to audit

Instead:

```text
LLM creates invalidation rules
Code evaluates invalidation rules
LLM creates a new plan when needed
```

---

# Cost Optimization Strategy

LLM Calls:

```text
create_plan
Every 15–60 min
```

```text
confirm_setup
Only when setup detected
```

No LLM calls during normal tick processing.

This minimizes cost.

---

# Recommended Tech Stack

## Minimal Version

```text
Python
Asyncio
Redis
Exchange WebSocket
```

---

## Scaling Version

```text
Python
Redis
Redis Streams / Kafka
Multiple Workers
PostgreSQL
```

---

# Mental Model

```text
create_plan     = strategist
rule_engine     = market watcher
confirm_setup   = tactical reviewer
risk_manager    = safety layer
executor        = trader
```

---

# Final Takeaway

```text
LLM defines the trading plan.

Rule Engine continuously monitors the market.

confirm_setup validates opportunities.

Risk Manager enforces constraints.

Executor places orders.
```

The architecture should be event-driven, versioned, rule-based, and use AI only where reasoning adds value.
