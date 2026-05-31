"""Deterministic plan/setup invalidation.

Invalidation is evaluated by CODE, never by asking the LLM every tick. Severity
levels follow the architecture doc:
- warning: monitor only (no action)
- soft:    pause new entries
- hard:    kill the plan, cancel its setups, request a replan

``on_close`` rules only fire on a closed candle so a brief intrabar wick can't
trip a hard invalidation.
"""

from __future__ import annotations

from typing import Any

from ats.engine.rule_engine import eval_rule

_SEVERITY_RANK = {"warning": 0, "soft": 1, "hard": 2}


def evaluate_invalidation(
    rules: list[dict[str, Any]],
    features: dict[str, Any],
    prev: dict[str, Any] | None = None,
    *,
    candle_closed: bool,
) -> str | None:
    """Return the highest-severity triggered level, or None.

    A rule with ``on_close=True`` is skipped unless ``candle_closed`` is True.
    """
    triggered: list[str] = []
    for rule in rules or []:
        if rule.get("on_close", True) and not candle_closed:
            continue
        if eval_rule(rule, features, prev):
            triggered.append(rule.get("severity", "warning"))
    if not triggered:
        return None
    return max(triggered, key=lambda s: _SEVERITY_RANK.get(s, 0))
