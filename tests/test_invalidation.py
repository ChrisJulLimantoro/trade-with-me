"""Tests for deterministic plan/setup invalidation."""

from __future__ import annotations

from ats.engine.invalidation import evaluate_invalidation

FEATURES = {"price": 100.0, "close": 100.0, "rsi_14": 55.0}


def test_no_rules_returns_none() -> None:
    assert evaluate_invalidation([], FEATURES, candle_closed=True) is None


def test_none_triggered_returns_none() -> None:
    rules = [{"severity": "hard", "left": "price", "operator": "<", "right": 90}]
    assert evaluate_invalidation(rules, FEATURES, candle_closed=True) is None


def test_highest_severity_wins() -> None:
    rules = [
        {"severity": "warning", "left": "rsi_14", "operator": ">", "right": 50, "on_close": False},
        {"severity": "soft", "left": "rsi_14", "operator": ">", "right": 50, "on_close": False},
        {"severity": "hard", "left": "rsi_14", "operator": ">", "right": 50, "on_close": False},
    ]
    assert evaluate_invalidation(rules, FEATURES, candle_closed=True) == "hard"


def test_soft_over_warning() -> None:
    rules = [
        {"severity": "warning", "left": "rsi_14", "operator": ">", "right": 50, "on_close": False},
        {"severity": "soft", "left": "rsi_14", "operator": ">", "right": 50, "on_close": False},
    ]
    assert evaluate_invalidation(rules, FEATURES, candle_closed=True) == "soft"


def test_on_close_rule_skipped_intrabar() -> None:
    rules = [{"severity": "hard", "left": "rsi_14", "operator": ">", "right": 50, "on_close": True}]
    # rule would fire (55 > 50) but on_close requires a closed candle
    assert evaluate_invalidation(rules, FEATURES, candle_closed=False) is None
    assert evaluate_invalidation(rules, FEATURES, candle_closed=True) == "hard"


def test_intrabar_rule_fires_without_close() -> None:
    rules = [
        {"severity": "soft", "left": "rsi_14", "operator": ">", "right": 50, "on_close": False}
    ]
    assert evaluate_invalidation(rules, FEATURES, candle_closed=False) == "soft"
