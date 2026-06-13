"""Tests for the hard-rule sanitizer in create_plan.

The entry_zone is the executable price gate, so bare ``price`` vs numeric-literal hard
rules are redundant-at-best / contradictory-at-worst and must be stripped, while genuine
feature conditions (``price`` vs an EMA, or non-price comparisons) are preserved.
"""

from __future__ import annotations

from ats.llm.schemas import Rule, SetupOutput
from ats.planning.create_plan import _stripped_hard_rules


def _setup(hard: list[Rule]) -> SetupOutput:
    return SetupOutput(
        direction="short",
        entry_zone=[61450.0, 61500.0],
        take_profit=[60800.0],
        stop_loss=61950.0,
        size_pct=0.05,
        hard_rules=hard,
    )


def test_strips_price_vs_literal_hard_rules() -> None:
    s = _setup(
        [
            Rule(left="price", operator=">=", right=61850.0),  # contradicts the zone
            Rule(left="price", operator="<=", right=61500.0),  # restates zone high
        ]
    )
    assert _stripped_hard_rules(s) == []


def test_keeps_price_vs_feature_and_non_price_rules() -> None:
    s = _setup(
        [
            Rule(left="price", operator=">=", right=61850.0),  # dropped
            Rule(left="price", operator="<", right="ema_50"),  # trend gate — kept
            Rule(left="rsi_14", operator="<", right=70.0),     # non-price — kept
        ]
    )
    kept = _stripped_hard_rules(s)
    assert {(r["left"], r["operator"], r["right"]) for r in kept} == {
        ("price", "<", "ema_50"),
        ("rsi_14", "<", 70.0),
    }


def test_empty_hard_rules_stay_empty() -> None:
    assert _stripped_hard_rules(_setup([])) == []
