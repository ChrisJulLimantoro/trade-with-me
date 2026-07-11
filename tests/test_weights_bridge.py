"""Weight invariants + Signal→PlanOutput bridge parity (spec 04 / Part 1 bridge)."""

from __future__ import annotations

import pytest

from ats.llm.schemas import SetupOutput
from ats.orchestration.weights import WEIGHTS, renormalize, weights_with_htf
from ats.risk.manager import reward_risk
from ats.strategy.bridge import signal_to_plan, signal_to_setup
from ats.synthesis.synthesizer import Signal


def test_weights_sum_to_one() -> None:
    assert round(sum(WEIGHTS.values()), 9) == 1.0
    assert len(WEIGHTS) == 9


def test_renormalize_after_drop_sums_to_one() -> None:
    active = set(WEIGHTS) - {"liquidity"}
    renorm = renormalize(active)
    assert set(renorm) == active
    assert round(sum(renorm.values()), 9) == 1.0
    # Relative ordering is preserved by pure arithmetic renormalization.
    assert renorm["structure"] > renorm["momentum"]


def test_renormalize_empty_raises() -> None:
    with pytest.raises(ValueError):
        renormalize(set())


def test_weights_with_htf_sums_to_one_and_sets_htf() -> None:
    w = weights_with_htf(0.15)
    assert round(sum(w.values()), 9) == 1.0
    assert w["htf_trend"] == 0.15
    assert set(w) == set(WEIGHTS)


def test_weights_with_htf_preserves_relative_shape() -> None:
    w = weights_with_htf(0.15)
    # Base (non-htf_trend) weights are uniformly scaled, so their pairwise ratios match WEIGHTS.
    for a, b in (("structure", "momentum"), ("momentum", "funding"), ("basis", "cvd")):
        assert w[a] / w[b] == pytest.approx(WEIGHTS[a] / WEIGHTS[b])


def test_weights_with_htf_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        weights_with_htf(0.0)
    with pytest.raises(ValueError):
        weights_with_htf(1.0)


def _signal() -> Signal:
    return Signal(
        direction="long",
        entry_zone=[99.5, 100.5],
        stop_loss=97.5,
        take_profit=[105.15, 108.4],
        confidence=0.76,
        size_pct=0.03,
        reasons=["breakout 2.5 ATR beyond range", "long confidence 0.76, rr 1.55"],
        agent_scores={"structure": 0.8, "momentum": 0.7},
        risk_reward=1.55,
        alignment_pen=0.004,
        worst_case_fill=100.5,
    )


def test_signal_to_setup_is_a_valid_setupoutput() -> None:
    setup = signal_to_setup(_signal(), {"rsi_14": 55, "macd_hist": 0.5})
    assert isinstance(setup, SetupOutput)
    assert setup.direction == "long"
    assert setup.entry_zone == [99.5, 100.5]
    assert setup.hard_rules == []  # entry_zone is the gate
    assert len(setup.soft_rules) == 2
    # The risk manager accepts it: worst-case-fill RR clears the floor.
    rr = reward_risk("long", setup.entry_zone[1], setup.stop_loss, list(setup.take_profit))
    assert rr >= 1.5


def test_signal_to_plan_none_is_stand_aside() -> None:
    plan = signal_to_plan(None, {})
    assert plan.market_bias == "neutral"
    assert plan.allowed_setups == []


def test_signal_to_plan_carries_setup_and_bias() -> None:
    plan = signal_to_plan(_signal(), {"rsi_14": 55, "macd_hist": 0.5})
    assert plan.market_bias == "bullish"
    assert len(plan.allowed_setups) == 1
    assert plan.rationale
