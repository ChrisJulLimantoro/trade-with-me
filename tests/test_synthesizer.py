"""Synthesizer behavior + determinism (spec 04 §Validation / Part 1 verification)."""

from __future__ import annotations

import dataclasses

from ats.agents.base import AgentScore
from ats.orchestration.weights import WEIGHTS
from ats.synthesis.synthesizer import synthesize

_KW = dict(
    weights=WEIGHTS,
    min_rr=1.5,
    min_confidence=0.55,
    min_stop_atr_mult=1.5,
    fee_bps=5.0,
    slippage_bps=2.0,
)
_FEATURES = {"close": 100.0, "atr_14": 2.0}


def _sc(name: str, score: float, direction: str, **md) -> AgentScore:
    return AgentScore(
        agent=name, score=score, direction=direction, deterministic_score=score, metadata=md
    )


def _long_scores() -> dict[str, AgentScore]:
    return {
        "structure": _sc("structure", 0.8, "long"),
        "momentum": _sc("momentum", 0.7, "long"),
    }


def test_weighted_majority_sets_direction() -> None:
    sig = synthesize(_long_scores(), {}, _FEATURES, [], **_KW)
    assert sig is not None and sig.direction == "long"
    assert sig.entry_zone[0] < sig.entry_zone[1]
    assert sig.risk_reward >= 1.5


def test_all_neutral_rejects() -> None:
    scores = {"structure": _sc("structure", 0.0, "neutral")}
    assert synthesize(scores, {}, _FEATURES, [], **_KW) is None


def test_alignment_penalty_lowers_confidence() -> None:
    # Same base confidence (equal weights, same weighted mean) but B has intra-side variance.
    aligned = {"funding": _sc("funding", 0.7, "long"), "basis": _sc("basis", 0.7, "long")}
    spread = {"funding": _sc("funding", 0.9, "long"), "basis": _sc("basis", 0.5, "long")}
    a = synthesize(aligned, {}, _FEATURES, [], **{**_KW, "min_confidence": 0.0})
    b = synthesize(spread, {}, _FEATURES, [], **{**_KW, "min_confidence": 0.0})
    assert a is not None and b is not None
    assert b.alignment_pen > a.alignment_pen
    assert (a.confidence - b.confidence) / a.confidence >= 0.05


def test_regime_modulation_reduces_long_in_bear_high() -> None:
    neutral = synthesize(_long_scores(), {"regime_cell": "side-low"}, _FEATURES, [], **_KW)
    bear = synthesize(_long_scores(), {"regime_cell": "bear-high"}, _FEATURES, [], **_KW)
    assert neutral is not None and bear is not None
    assert bear.confidence < neutral.confidence


def test_rr_floor_rejects() -> None:
    assert synthesize(_long_scores(), {}, _FEATURES, [], **{**_KW, "min_rr": 100.0}) is None


def test_confidence_threshold_rejects() -> None:
    assert synthesize(_long_scores(), {}, _FEATURES, [], **{**_KW, "min_confidence": 0.99}) is None


def test_fvg_override_applied_when_qualified() -> None:
    scores = _long_scores()
    scores["price_action"] = _sc("price_action", 0.8, "long", fvg_zone=[100.0, 100.6])
    sig = synthesize(scores, {}, _FEATURES, [], **_KW)
    assert sig is not None and sig.fvg_override is True
    assert sig.entry_zone == [100.0, 100.6]
    assert any("FVG zone" in r for r in sig.reasons)


def test_fvg_override_skipped_below_threshold() -> None:
    scores = _long_scores()
    scores["price_action"] = _sc("price_action", 0.5, "long", fvg_zone=[100.0, 100.6])
    sig = synthesize(scores, {}, _FEATURES, [], **_KW)
    assert sig is not None and sig.fvg_override is False
    assert sig.entry_zone == [99.5, 100.5]  # default close ± 0.25 ATR band


def test_identical_input_is_byte_identical() -> None:
    a = synthesize(_long_scores(), {"regime_cell": "bull-low"}, _FEATURES, [], **_KW)
    b = synthesize(_long_scores(), {"regime_cell": "bull-low"}, _FEATURES, [], **_KW)
    assert dataclasses.asdict(a) == dataclasses.asdict(b)
