"""Deterministic rule engine — the real-time detector.

Evaluates a plan's setups against a feature snapshot WITHOUT any LLM call. A rule is
a plain dict ``{"left", "operator", "right", "weight"?}`` (the JSON the LLM emits).
Operands resolve to a feature value, the literal token ``"price"``, or a number.

Pure and DB-free, so it unit-tests like the ``processing`` indicators.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ats.logging import get_logger

log = get_logger(__name__)

_NUMERIC_OPS = {
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}
_CROSS_OPS = {"crosses_above", "crosses_below"}


def resolve_operand(operand: float | int | str, features: dict[str, Any]) -> float | None:
    """Resolve a rule operand to a number.

    Numbers are literals. The string ``"price"`` resolves to the current close.
    Any other string is a feature-column name. Missing / NaN → ``None`` (the rule
    will then be treated as False by the caller).
    """
    if isinstance(operand, bool):  # guard: bool is an int subclass
        return float(operand)
    if isinstance(operand, (int, float)):
        return float(operand)
    val = features.get(operand)
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _record_unresolved(
    raw: float | int | str, features: dict[str, Any], sink: list[str] | None
) -> None:
    """Note a string operand (a feature reference) that didn't resolve to a number.

    ``missing`` = the column isn't in the feature row at all (often pruned because it is
    unavailable, or an LLM-hallucinated name); ``nan`` = present but null/NaN. Numbers and
    the literal ``price`` token are never recorded.
    """
    if sink is None or not isinstance(raw, str) or raw == "price":
        return
    sink.append(f"{raw}:{'nan' if raw in features else 'missing'}")


def eval_rule(
    rule: dict[str, Any],
    features: dict[str, Any],
    prev: dict[str, Any] | None = None,
    *,
    unresolved: list[str] | None = None,
) -> bool:
    """Evaluate one rule to a bool. Unresolvable operands → False (conservative).

    When ``unresolved`` is given, any string operand that fails to resolve is appended to
    it so callers can surface rules anchored on missing/NaN features instead of letting
    them silently fail closed.
    """
    op = rule.get("operator")
    left_raw, right_raw = rule.get("left"), rule.get("right")

    if op in _CROSS_OPS:
        if prev is None:
            return False
        cur_l, cur_r = resolve_operand(left_raw, features), resolve_operand(right_raw, features)
        prev_l, prev_r = resolve_operand(left_raw, prev), resolve_operand(right_raw, prev)
        if None in (cur_l, cur_r, prev_l, prev_r):
            if cur_l is None or prev_l is None:
                _record_unresolved(left_raw, features, unresolved)
            if cur_r is None or prev_r is None:
                _record_unresolved(right_raw, features, unresolved)
            return False
        if op == "crosses_above":
            return prev_l <= prev_r and cur_l > cur_r
        return prev_l >= prev_r and cur_l < cur_r

    fn = _NUMERIC_OPS.get(op)
    if fn is None:
        log.warning("rule_unknown_operator", operator=op)
        return False
    left, right = resolve_operand(left_raw, features), resolve_operand(right_raw, features)
    if left is None or right is None:
        if left is None:
            _record_unresolved(left_raw, features, unresolved)
        if right is None:
            _record_unresolved(right_raw, features, unresolved)
        return False
    return bool(fn(left, right))


def eval_hard_rules(
    rules: list[dict[str, Any]],
    features: dict[str, Any],
    prev: dict[str, Any] | None = None,
    *,
    unresolved: list[str] | None = None,
) -> tuple[bool, list[str]]:
    """All hard rules must pass. Returns (all_passed, list of failed rule descriptions)."""
    failed = [
        f"{r.get('left')} {r.get('operator')} {r.get('right')}"
        for r in rules
        if not eval_rule(r, features, prev, unresolved=unresolved)
    ]
    return (len(failed) == 0), failed


def score_soft_rules(
    rules: list[dict[str, Any]],
    features: dict[str, Any],
    prev: dict[str, Any] | None = None,
    *,
    unresolved: list[str] | None = None,
) -> float:
    """Weighted fraction of soft rules that pass. No soft rules → 1.0 (neutral)."""
    if not rules:
        return 1.0
    total = sum(float(r.get("weight") or 1.0) for r in rules)
    if total <= 0:
        return 1.0
    passed = sum(
        float(r.get("weight") or 1.0)
        for r in rules
        if eval_rule(r, features, prev, unresolved=unresolved)
    )
    return passed / total


def in_entry_zone(price: float | None, low: float, high: float) -> bool:
    return price is not None and low <= price <= high


def _entry_trigger(
    setup: dict[str, Any],
    features: dict[str, Any],
    *,
    entry_trigger_mode: str,
) -> tuple[bool, float | None, float | None]:
    """Return (triggered, fill_price, close_price) for the setup's entry zone."""
    low, high = float(setup["entry_zone_low"]), float(setup["entry_zone_high"])
    close = resolve_operand("price", features)
    if in_entry_zone(close, low, high):
        return True, close, close
    if entry_trigger_mode != "wick_limit":
        return False, close, close

    candle_low = resolve_operand("low", features)
    candle_high = resolve_operand("high", features)
    if candle_low is None or candle_high is None:
        return False, close, close
    if candle_high < low or candle_low > high:
        return False, close, close

    direction = str(setup.get("direction") or "").lower()
    fill = low if direction == "short" else high
    return True, fill, close


@dataclass
class SetupEval:
    detected: bool
    price_ok: bool
    hard_ok: bool
    soft_score: float
    trigger_price: float | None
    close_price: float | None
    price: float | None
    failed_hard: list[str] = field(default_factory=list)
    # Feature references (in hard/soft rules) that didn't resolve this bar, as
    # "<name>:missing" / "<name>:nan" — surfaces rules anchored on dead columns.
    unresolved_operands: list[str] = field(default_factory=list)


def evaluate_setup(
    setup: dict[str, Any],
    features: dict[str, Any],
    prev: dict[str, Any] | None = None,
    *,
    soft_threshold: float,
    entry_trigger_mode: str = "close",
) -> SetupEval:
    """Combine entry-zone + hard rules + soft threshold into a detection decision.

    ``setup`` is a dict with keys ``entry_zone_low``, ``entry_zone_high``,
    ``hard_rules``, ``soft_rules``. ``features`` must include ``price`` (the close).
    ``SetupEval.price`` is the simulated entry price, which can differ from the close
    when ``entry_trigger_mode="wick_limit"`` and only the candle range touched the zone.
    """
    unresolved: list[str] = []
    price_ok, trigger_price, close_price = _entry_trigger(
        setup, features, entry_trigger_mode=entry_trigger_mode
    )
    hard_ok, failed = eval_hard_rules(
        setup.get("hard_rules") or [], features, prev, unresolved=unresolved
    )
    soft_score = score_soft_rules(
        setup.get("soft_rules") or [], features, prev, unresolved=unresolved
    )
    detected = price_ok and hard_ok and soft_score >= soft_threshold
    return SetupEval(
        detected=detected,
        price_ok=price_ok,
        hard_ok=hard_ok,
        soft_score=soft_score,
        trigger_price=trigger_price,
        close_price=close_price,
        price=trigger_price,
        failed_hard=failed,
        unresolved_operands=sorted(set(unresolved)),
    )
