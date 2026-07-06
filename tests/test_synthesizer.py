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
    # Pullback band for a long sits BELOW close: fill (zone high) = close - 0.25 ATR = 99.5,
    # extending another 2×0.25 ATR down to 98.5 (limit-buy-the-dip).
    assert sig.entry_zone == [98.5, 99.5]


def test_identical_input_is_byte_identical() -> None:
    a = synthesize(_long_scores(), {"regime_cell": "bull-low"}, _FEATURES, [], **_KW)
    b = synthesize(_long_scores(), {"regime_cell": "bull-low"}, _FEATURES, [], **_KW)
    assert dataclasses.asdict(a) == dataclasses.asdict(b)


# --- min_voting_agents (i11 mechanism, default 0 = OFF) -------------------------------


def test_min_voting_agents_off_by_default() -> None:
    # default min_voting_agents=0 → a lone-agent signal still synthesizes
    lone = {"htf_trend": _sc("htf_trend", 1.0, "long")}
    sig = synthesize(lone, {}, _FEATURES, [], **_KW)
    assert sig is not None and sig.direction == "long"


def test_min_voting_agents_rejects_lone_agent() -> None:
    # 1 voter below min_voting_agents=2 → rejected
    lone = {"htf_trend": _sc("htf_trend", 1.0, "long")}
    assert synthesize(lone, {}, _FEATURES, [], **{**_KW, "min_voting_agents": 2}) is None


def test_min_voting_agents_passes_two_voters() -> None:
    # 2 voters ≥ min_voting_agents=2 → passes
    two = {
        "htf_trend": _sc("htf_trend", 0.8, "long"),
        "momentum": _sc("momentum", 0.7, "long"),
    }
    sig = synthesize(two, {}, _FEATURES, [], **{**_KW, "min_voting_agents": 2})
    assert sig is not None and sig.direction == "long"


def test_min_voting_agents_counts_chosen_direction_only() -> None:
    # 2 long + 1 short, min=3 → rejected for long (only 2 long voters < 3)
    mixed = {
        "htf_trend": _sc("htf_trend", 0.8, "long"),
        "momentum": _sc("momentum", 0.7, "long"),
        "cvd": _sc("cvd", 0.6, "short"),
    }
    assert synthesize(mixed, {}, _FEATURES, [], **{**_KW, "min_voting_agents": 3}) is None


def test_min_voting_agents_neutral_voters_not_counted() -> None:
    # 2 long voters + 6 neutral abstentions → passes at min=2 (neutrals don't count)
    two_plus_neutral = {
        "htf_trend": _sc("htf_trend", 0.8, "long"),
        "momentum": _sc("momentum", 0.7, "long"),
        "structure": _sc("structure", 0.0, "neutral"),
        "funding": _sc("funding", 0.0, "neutral"),
        "liquidity": _sc("liquidity", 0.0, "neutral"),
        "price_action": _sc("price_action", 0.0, "neutral"),
        "cross_venue": _sc("cross_venue", 0.0, "neutral"),
        "basis": _sc("basis", 0.0, "neutral"),
    }
    sig = synthesize(two_plus_neutral, {}, _FEATURES, [], **{**_KW, "min_voting_agents": 2})
    assert sig is not None and sig.direction == "long"
