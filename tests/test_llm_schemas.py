"""Tests for LLM I/O schemas and the deterministic mock client."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ats.llm.client import MockClient, get_client
from ats.llm.schemas import (
    ConfirmOutput,
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


def test_confirm_output_defaults() -> None:
    c = ConfirmOutput(action="CONFIRM")
    assert c.size_multiplier == 1.0


def test_get_client_returns_mock_by_default() -> None:
    # llm_mock defaults True in Settings
    assert isinstance(get_client(), MockClient)


async def test_mock_create_plan_is_schema_valid() -> None:
    client = MockClient()
    envelope = {
        "features": {
            "close": 50000, "rsi_14": 55, "macd_hist": 1.2, "ema_50": 49000, "ema_200": 48000,
        },
        "regime": {"trend": "bull"},
        "risk_limits": {"risk_per_trade_pct": 0.01, "max_leverage": 3.0},
    }
    plan, result = await client.create_plan(envelope, symbol="BTCUSDT")
    assert isinstance(plan, PlanOutput)
    assert result.parse_ok and result.mock and result.cost_usd == 0.0
    assert plan.market_bias == "bullish" and len(plan.allowed_setups) == 1
    assert plan.allowed_setups[0].direction == "long"


async def test_mock_neutral_regime_has_no_setups() -> None:
    client = MockClient()
    envelope = {"features": {"close": 100}, "regime": {"trend": "side"}, "risk_limits": {}}
    plan, _ = await client.create_plan(envelope, symbol="BTCUSDT")
    assert plan.market_bias == "neutral" and plan.allowed_setups == []


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


def test_confirm_size_multiplier_normalized() -> None:
    assert ConfirmOutput.model_validate({"action": "CONFIRM", "size_multiplier": 0}).size_multiplier == 1.0
    assert ConfirmOutput.model_validate({"action": "CONFIRM", "size_multiplier": None}).size_multiplier == 1.0
    assert ConfirmOutput(action="REDUCE_SIZE", size_multiplier=1.4).size_multiplier == 1.0


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


async def test_mock_confirm_thresholds() -> None:
    client = MockClient()
    strong, _ = await client.confirm_setup({"rule_eval": {"soft_score": 0.9}}, symbol="X")
    marginal, _ = await client.confirm_setup({"rule_eval": {"soft_score": 0.6}}, symbol="X")
    weak, _ = await client.confirm_setup({"rule_eval": {"soft_score": 0.2}}, symbol="X")
    assert strong.action == "CONFIRM"
    assert marginal.action == "REDUCE_SIZE" and marginal.size_multiplier == 0.5
    assert weak.action == "WAIT"
