"""Static system prompts + user-message rendering for the LLM-plan layer.

The system prompts are constant per call kind, so they form a stable cacheable
prefix (OpenAI caches long identical prefixes automatically). Only the per-call
envelope (the user message) varies. The LLM only ever sees structured JSON — no
raw price prose — and must answer with JSON matching the Pydantic schema.
"""

from __future__ import annotations

import json
from typing import Any

# The rule grammar shared by hard/soft/invalidation rules. Kept terse and explicit
# so the model emits rules the deterministic engine can evaluate without ambiguity.
_RULE_GRAMMAR = """\
A rule is an object {"left", "operator", "right", "weight"?}.
- "left"/"right" are EITHER a feature name (e.g. "rsi_14", "ema_50", "macd_hist"),
  the literal token "price" (the current close), or a number.
- "operator" is one of: ">", "<", ">=", "<=", "==", "!=", "crosses_above", "crosses_below".
- "weight" (0..1) applies ONLY to soft rules and scales their contribution.
Hard rules MUST all pass for a setup to trigger. Soft rules contribute a weighted
confidence score. Invalidation rules carry a "severity" ("warning"|"soft"|"hard")
and "on_close" (true = only fire on a closed candle)."""

PLAN_SYSTEM_PROMPT = f"""\
You are the strategist in a crypto perpetuals trading system. You decide WHAT to
trade; deterministic code decides WHEN. You are given a structured market snapshot
(indicators, regime, recent OHLCV summary, portfolio, risk limits) as JSON.

Produce a trading plan: an overall market_bias and zero or more allowed_setups.
Each setup must be directly executable by a deterministic rule engine:
- direction: "long" or "short"
- entry_zone: [low, high] price band that must CONTAIN the relevant entries
- take_profit: list of target prices; stop_loss: a single price
- size_pct: fraction of equity (0..1), respect the risk_limits.max_position_pct
- hard_rules / soft_rules / invalidation_rules using the grammar below

{_RULE_GRAMMAR}

Rules:
- This is PAPER trading only. Never suggest leverage. Never invent data.
- Keep reward:risk at or above risk_limits.min_rr. If conditions are unclear,
  return market_bias and an EMPTY allowed_setups list rather than forcing a trade.
- Respond ONLY with JSON matching the required schema."""

CONFIRM_SYSTEM_PROMPT = """\
You are the tactical reviewer in a crypto perpetuals trading system. A deterministic
rule engine has just detected that one of the active plan's setups met its entry
conditions. You are given the setup, the current rule evaluation, and a fresh feature
snapshot as JSON.

Decide one action:
- "CONFIRM": conditions still match the plan; execute as sized.
- "REDUCE_SIZE": execute but smaller (set size_multiplier in (0, 1]).
- "WAIT": conditions are marginal; do not execute this bar.
- "REJECT": conditions contradict the plan; do not execute.

This is PAPER trading only. Respond ONLY with JSON matching the required schema."""


def user_message(envelope: dict[str, Any]) -> str:
    """Render the per-call envelope as a compact JSON user message."""
    return json.dumps(envelope, default=str, separators=(",", ":"))
