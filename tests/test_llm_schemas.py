"""Tests for LLM I/O schemas and the deterministic mock client."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ats.llm.client import MockClient, get_client
from ats.llm.schemas import (
    AdjudicationOutput,
    ObservationOutput,
    PlanOutput,
    ReflectionOutput,
    Rule,
    SetupOutput,
)

VALID_SETUP = {
    "direction": "long",
    "entry_zone": [100.0, 110.0],
    "take_profit": [120.0],
    "stop_loss": 95.0,
    "size_pct": 0.05,
}


def test_valid_plan_parses() -> None:
    setup = SetupOutput(
        **VALID_SETUP, hard_rules=[Rule(left="price", operator=">", right="ema_50")]
    )
    plan = PlanOutput(market_bias="bullish", allowed_setups=[setup])
    assert plan.market_bias == "bullish" and len(plan.allowed_setups) == 1


def test_size_pct_over_one_is_clamped() -> None:
    # size_pct is advisory (risk sizing overrides it), so an overshoot is clamped, not rejected.
    assert SetupOutput(**{**VALID_SETUP, "size_pct": 1.5}).size_pct == 1.0


def test_size_pct_non_positive_falls_back_to_nominal() -> None:
    assert SetupOutput(**{**VALID_SETUP, "size_pct": 0}).size_pct == 0.05
    assert SetupOutput(**{**VALID_SETUP, "size_pct": -2}).size_pct == 0.05


def test_reject_empty_take_profit() -> None:
    with pytest.raises(ValidationError):
        SetupOutput(**{**VALID_SETUP, "take_profit": []})


def test_reject_inverted_entry_zone() -> None:
    with pytest.raises(ValidationError):
        SetupOutput(**{**VALID_SETUP, "entry_zone": [110.0, 100.0]})


def test_reject_bad_operator() -> None:
    with pytest.raises(ValidationError):
        Rule(left="price", operator="~=", right=5)


def test_adjudication_defaults_are_neutral_no_op() -> None:
    a = AdjudicationOutput()
    assert a.confidence_delta == 0.0 and a.bias == "neutral"
    assert a.no_trade is False and a.reasons == []


def test_adjudication_coerces_nulls_and_string_reasons() -> None:
    a = AdjudicationOutput.model_validate(
        {"confidence_delta": None, "bias": None, "no_trade": None, "reasons": "one phrase"}
    )
    assert a.confidence_delta == 0.0
    assert a.bias == "neutral"
    assert a.no_trade is False
    assert a.reasons == ["one phrase"]


def test_adjudication_keeps_raw_delta_unclamped() -> None:
    # The ±0.20 bound is applied in code (strategy.adjudication), not the schema, so the raw
    # model value survives parse and the clamp stays auditable.
    assert AdjudicationOutput(confidence_delta=0.9).confidence_delta == 0.9
    assert AdjudicationOutput(confidence_delta=-0.7).confidence_delta == -0.7


def test_get_client_returns_mock_by_default() -> None:
    # llm_mock defaults True in Settings
    assert isinstance(get_client(), MockClient)


async def test_mock_adjudicate_is_delta_zero_baseline() -> None:
    client = MockClient()
    envelope = {"signal": {"direction": "long", "reasons": ["breakout", "momentum"]}}
    adj, result = await client.adjudicate(envelope, symbol="BTCUSDT")
    assert isinstance(adj, AdjudicationOutput)
    assert result.parse_ok and result.mock and result.cost_usd == 0.0
    # Pure deterministic baseline: no nudge, no veto, bias agrees with the signal direction.
    assert adj.confidence_delta == 0.0
    assert adj.no_trade is False
    assert adj.bias == "bullish"
    assert adj.reasons == ["breakout", "momentum"]


def test_observation_null_confidence_coerced_to_zero() -> None:
    """Models sometimes return 'confidence': null for non-EXIT_NOW actions.

    Pydantic treats explicit null as None, not as absent (which would use the default).
    The field_validator must coerce it to 0.0 so parse doesn't fail.
    """
    obs = ObservationOutput.model_validate({"action": "HOLD", "reason": "fine", "confidence": None})
    assert obs.confidence == 0.0

    # Also via JSON (the real parse path)
    obs2 = ObservationOutput.model_validate_json('{"action":"TIGHTEN_STOP","confidence":null}')
    assert obs2.confidence == 0.0


def test_observation_scale_frac_zero_coerced_to_none() -> None:
    """Models emit scale_frac:0 on non-SCALE_OUT actions; a bare gt=0 field would reject it."""
    assert ObservationOutput.model_validate({"action": "HOLD", "scale_frac": 0}).scale_frac is None
    assert (
        ObservationOutput.model_validate_json('{"action":"HOLD","scale_frac":0.0}').scale_frac
        is None
    )
    assert ObservationOutput(action="SCALE_OUT", scale_frac=1.5).scale_frac == 1.0


def test_observation_confidence_out_of_range_clamped() -> None:
    assert ObservationOutput(action="EXIT_NOW", confidence=1.3).confidence == 1.0
    assert ObservationOutput(action="EXIT_NOW", confidence=-0.4).confidence == 0.0


def test_observation_null_reason_coerced() -> None:
    assert ObservationOutput.model_validate({"action": "HOLD", "reason": None}).reason == ""


def test_reflection_fields_normalized() -> None:
    r = ReflectionOutput.model_validate(
        {
            "category": "clean_win",
            "hypothesis": None,
            "proposed_adjustment": "x" * 300,
            "confidence_in_lesson": 1.7,
        }
    )
    assert r.hypothesis == ""
    assert len(r.proposed_adjustment) == 200
    assert r.confidence_in_lesson == 1.0
