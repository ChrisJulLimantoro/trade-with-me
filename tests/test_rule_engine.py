"""Tests for the deterministic rule engine."""

from __future__ import annotations

import math

import pytest

from ats.engine.rule_engine import (
    eval_hard_rules,
    eval_rule,
    evaluate_setup,
    in_entry_zone,
    resolve_operand,
    score_soft_rules,
)

FEATURES = {"price": 100.0, "close": 100.0, "rsi_14": 55.0, "ema_50": 98.0, "macd_hist": 0.5}


@pytest.mark.parametrize(
    "operator,right,expected",
    [
        (">", 90, True), (">", 110, False),
        ("<", 110, True), ("<", 90, False),
        (">=", 100, True), ("<=", 100, True),
        ("==", 100, True), ("!=", 100, False),
    ],
)
def test_numeric_operators(operator: str, right: float, expected: bool) -> None:
    assert eval_rule({"left": "price", "operator": operator, "right": right}, FEATURES) is expected


def test_left_and_right_can_be_feature_names() -> None:
    # price (100) > ema_50 (98)
    assert eval_rule({"left": "price", "operator": ">", "right": "ema_50"}, FEATURES) is True
    assert eval_rule({"left": "ema_50", "operator": ">", "right": "price"}, FEATURES) is False


def test_price_token_resolves_to_close() -> None:
    assert resolve_operand("price", FEATURES) == 100.0


def test_missing_feature_makes_rule_false() -> None:
    assert eval_rule({"left": "nonexistent", "operator": ">", "right": 5}, FEATURES) is False


def test_nan_feature_makes_rule_false() -> None:
    feats = {**FEATURES, "rsi_14": math.nan}
    assert eval_rule({"left": "rsi_14", "operator": ">", "right": 50}, feats) is False


def test_unknown_operator_is_false() -> None:
    assert eval_rule({"left": "price", "operator": "~=", "right": 100}, FEATURES) is False


def test_crosses_above_needs_prev() -> None:
    prev = {"rsi_14": 45.0}
    cur = {"rsi_14": 55.0}
    rule = {"left": "rsi_14", "operator": "crosses_above", "right": 50}
    assert eval_rule(rule, cur, prev) is True
    assert eval_rule(rule, cur, None) is False  # no prev → False
    # already-above on prev bar → not a fresh cross
    assert eval_rule(rule, cur, {"rsi_14": 51.0}) is False


def test_crosses_below() -> None:
    rule = {"left": "rsi_14", "operator": "crosses_below", "right": 50}
    assert eval_rule(rule, {"rsi_14": 45.0}, {"rsi_14": 55.0}) is True
    assert eval_rule(rule, {"rsi_14": 55.0}, {"rsi_14": 45.0}) is False


def test_hard_rules_all_must_pass() -> None:
    ok, failed = eval_hard_rules(
        [
            {"left": "price", "operator": ">", "right": "ema_50"},  # pass
            {"left": "rsi_14", "operator": "<", "right": 50},  # fail (55 < 50 is False)
        ],
        FEATURES,
    )
    assert ok is False
    assert len(failed) == 1


def test_soft_score_weighted_fraction() -> None:
    # rsi_14>50 (w=0.6) passes; macd_hist<0 (w=0.4) fails → 0.6 / 1.0
    rules = [
        {"left": "rsi_14", "operator": ">", "right": 50, "weight": 0.6},
        {"left": "macd_hist", "operator": "<", "right": 0, "weight": 0.4},
    ]
    assert score_soft_rules(rules, FEATURES) == pytest.approx(0.6)


def test_soft_score_no_rules_is_one() -> None:
    assert score_soft_rules([], FEATURES) == 1.0


def test_soft_score_default_weight_is_one() -> None:
    rules = [
        {"left": "rsi_14", "operator": ">", "right": 50},  # pass
        {"left": "macd_hist", "operator": "<", "right": 0},  # fail
    ]
    assert score_soft_rules(rules, FEATURES) == pytest.approx(0.5)


@pytest.mark.parametrize(
    "price,low,high,expected",
    [(100, 95, 105, True), (95, 95, 105, True), (105, 95, 105, True), (94.9, 95, 105, False)],
)
def test_in_entry_zone_boundaries(price: float, low: float, high: float, expected: bool) -> None:
    assert in_entry_zone(price, low, high) is expected


def test_evaluate_setup_detection() -> None:
    setup = {
        "entry_zone_low": 95,
        "entry_zone_high": 105,
        "hard_rules": [{"left": "price", "operator": ">", "right": "ema_50"}],
        "soft_rules": [{"left": "rsi_14", "operator": ">", "right": 50}],
    }
    ev = evaluate_setup(setup, FEATURES, soft_threshold=0.5)
    assert ev.detected is True and ev.price_ok and ev.hard_ok and ev.soft_score == 1.0
    assert ev.price == 100.0 and ev.trigger_price == 100.0 and ev.close_price == 100.0


def test_close_inside_zone_triggers_in_both_entry_modes() -> None:
    setup = {"direction": "long", "entry_zone_low": 95, "entry_zone_high": 105}
    for mode in ("close", "wick_limit"):
        ev = evaluate_setup(setup, FEATURES, soft_threshold=0.5, entry_trigger_mode=mode)
        assert ev.detected is True
        assert ev.price == 100.0


def test_wick_touch_only_triggers_in_wick_limit_mode() -> None:
    setup = {"direction": "long", "entry_zone_low": 101, "entry_zone_high": 105}
    features = {**FEATURES, "price": 100.0, "close": 100.0, "low": 99.0, "high": 102.0}

    close_ev = evaluate_setup(setup, features, soft_threshold=0.5, entry_trigger_mode="close")
    wick_ev = evaluate_setup(setup, features, soft_threshold=0.5, entry_trigger_mode="wick_limit")

    assert close_ev.detected is False
    assert wick_ev.detected is True
    assert wick_ev.close_price == 100.0


def test_wick_limit_long_uses_zone_high_as_fill() -> None:
    setup = {"direction": "long", "entry_zone_low": 101, "entry_zone_high": 105}
    features = {**FEATURES, "price": 100.0, "close": 100.0, "low": 99.0, "high": 102.0}

    ev = evaluate_setup(setup, features, soft_threshold=0.5, entry_trigger_mode="wick_limit")

    assert ev.detected is True
    assert ev.price == 105.0 and ev.trigger_price == 105.0


def test_wick_limit_short_uses_zone_low_as_fill() -> None:
    setup = {"direction": "short", "entry_zone_low": 95, "entry_zone_high": 99}
    features = {**FEATURES, "price": 100.0, "close": 100.0, "low": 98.0, "high": 101.0}

    ev = evaluate_setup(setup, features, soft_threshold=0.5, entry_trigger_mode="wick_limit")

    assert ev.detected is True
    assert ev.price == 95.0 and ev.trigger_price == 95.0


def test_wick_limit_does_not_trigger_without_range_intersection() -> None:
    setup = {"direction": "long", "entry_zone_low": 110, "entry_zone_high": 115}
    features = {**FEATURES, "price": 100.0, "close": 100.0, "low": 99.0, "high": 105.0}

    ev = evaluate_setup(setup, features, soft_threshold=0.5, entry_trigger_mode="wick_limit")

    assert ev.detected is False and ev.price_ok is False


def test_evaluate_setup_not_detected_when_hard_fails() -> None:
    setup = {
        "entry_zone_low": 95,
        "entry_zone_high": 105,
        "hard_rules": [{"left": "price", "operator": "<", "right": "ema_50"}],  # fails
        "soft_rules": [],
    }
    ev = evaluate_setup(setup, FEATURES, soft_threshold=0.5)
    assert ev.detected is False and ev.hard_ok is False


def test_evaluate_setup_not_detected_out_of_zone() -> None:
    setup = {"entry_zone_low": 200, "entry_zone_high": 300, "hard_rules": [], "soft_rules": []}
    ev = evaluate_setup(setup, FEATURES, soft_threshold=0.5)
    assert ev.detected is False and ev.price_ok is False
