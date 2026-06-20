"""Tests for the plan-time bounded veto/bias (spec 07, Part 2 Role 1).

Spec 07 acceptance: the applied confidence delta is always within ±0.20, and the LLM never
changes direction / entry / stop / take_profit. ``no_trade`` and a sub-threshold final
confidence both veto. The deterministic levels are copied through untouched.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from ats.llm.schemas import AdjudicationOutput
from ats.strategy.adjudication import (
    CONFIDENCE_DELTA_CLAMP,
    apply_adjudication,
)
from ats.synthesis.synthesizer import Signal, size_for

MIN_CONF = 0.55


def _signal(confidence: float = 0.70) -> Signal:
    return Signal(
        direction="long",
        entry_zone=[100.0, 101.0],
        stop_loss=98.0,
        take_profit=[104.0, 106.0],
        confidence=confidence,
        size_pct=size_for(confidence),
        reasons=["deterministic breakout", "momentum with trend"],
        agent_scores={"structure": 0.8, "momentum": 0.7},
        risk_reward=2.0,
        alignment_pen=0.05,
        fvg_override=False,
        worst_case_fill=101.0,
        metadata={"regime_cell": "bull-low"},
    )


@pytest.mark.parametrize("raw_delta", [0.5, -0.5, 0.9, -0.7, 0.2, -0.2, 0.05, -0.05, 1000.0])
def test_applied_delta_never_exceeds_budget(raw_delta: float) -> None:
    sig = _signal(0.70)
    out = apply_adjudication(
        sig, AdjudicationOutput(confidence_delta=raw_delta), min_confidence=0.0
    )
    assert out is not None
    applied = out.confidence - sig.confidence
    assert -CONFIDENCE_DELTA_CLAMP - 1e-9 <= applied <= CONFIDENCE_DELTA_CLAMP + 1e-9


def test_hundred_extreme_deltas_all_bounded() -> None:
    # Spec 07 acceptance: 100 mocked calls returning ±0.5 → every applied delta ∈ [−0.2, 0.2].
    sig = _signal(0.50)
    for i in range(100):
        raw = 0.5 if i % 2 == 0 else -0.5
        out = apply_adjudication(
            sig, AdjudicationOutput(confidence_delta=raw), min_confidence=0.0
        )
        assert out is not None
        assert abs(out.confidence - sig.confidence) <= CONFIDENCE_DELTA_CLAMP + 1e-9


@pytest.mark.parametrize("raw_delta", [0.5, -0.5, 0.1, -0.1])
def test_direction_and_levels_never_change(raw_delta: float) -> None:
    sig = _signal(0.70)
    out = apply_adjudication(
        sig,
        AdjudicationOutput(confidence_delta=raw_delta, bias="bearish"),
        min_confidence=0.0,
    )
    assert out is not None
    assert out.direction == sig.direction
    assert out.entry_zone == sig.entry_zone
    assert out.stop_loss == sig.stop_loss
    assert out.take_profit == sig.take_profit


def test_no_trade_flag_vetoes() -> None:
    sig = _signal(0.90)  # would easily clear the threshold
    out = apply_adjudication(
        sig, AdjudicationOutput(no_trade=True), min_confidence=MIN_CONF
    )
    assert out is None


def test_sub_threshold_final_confidence_vetoes() -> None:
    sig = _signal(0.60)
    # -0.20 (max negative) → 0.40 < 0.55 threshold → veto.
    out = apply_adjudication(
        sig, AdjudicationOutput(confidence_delta=-0.5), min_confidence=MIN_CONF
    )
    assert out is None


def test_positive_delta_rederives_size_from_final_confidence() -> None:
    sig = _signal(0.78)  # size tier 0.03
    out = apply_adjudication(
        sig, AdjudicationOutput(confidence_delta=0.5), min_confidence=MIN_CONF
    )
    assert out is not None
    assert out.confidence == pytest.approx(0.98)  # 0.78 + clamp(0.5)=0.20
    assert out.size_pct == size_for(0.98)
    assert out.size_pct > sig.size_pct


def test_reasons_replaced_when_judge_supplies_them() -> None:
    sig = _signal(0.70)
    out = apply_adjudication(
        sig,
        AdjudicationOutput(confidence_delta=0.0, reasons=["judge: HTF aligned"]),
        min_confidence=MIN_CONF,
    )
    assert out is not None
    assert out.reasons == ["judge: HTF aligned"]


def test_reasons_fall_back_to_signal_when_judge_silent() -> None:
    sig = _signal(0.70)
    out = apply_adjudication(
        sig, AdjudicationOutput(confidence_delta=0.0, reasons=[]), min_confidence=MIN_CONF
    )
    assert out is not None
    assert out.reasons == sig.reasons


def test_disagreeing_bias_is_advisory_not_a_veto() -> None:
    sig = _signal(0.70)
    out = apply_adjudication(
        sig,
        AdjudicationOutput(confidence_delta=0.0, bias="bearish"),  # disagrees with long
        min_confidence=MIN_CONF,
    )
    # Bias is advisory: the trade is not flipped or vetoed, just flagged in metadata.
    assert out is not None
    assert out.direction == "long"
    assert out.metadata["adjudication_bias_agrees"] is False


def test_zero_delta_is_pure_passthrough_confidence() -> None:
    # The mock returns delta 0 — the deterministic baseline must be untouched bar metadata.
    sig = _signal(0.72)
    out = apply_adjudication(
        sig, AdjudicationOutput(confidence_delta=0.0), min_confidence=MIN_CONF
    )
    assert out is not None
    assert out.confidence == sig.confidence
    assert out.size_pct == sig.size_pct
    assert replace(out, metadata={}, reasons=sig.reasons) == replace(sig, metadata={})
