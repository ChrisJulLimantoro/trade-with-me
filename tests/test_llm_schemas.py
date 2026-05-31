"""Tests for LLM I/O schemas and the deterministic mock client."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ats.llm.client import MockClient, get_client
from ats.llm.schemas import ConfirmOutput, PlanOutput, Rule, SetupOutput

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


def test_reject_size_pct_over_one() -> None:
    with pytest.raises(ValidationError):
        SetupOutput(**{**VALID_SETUP, "size_pct": 1.5})


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
        "risk_limits": {"max_position_pct": 0.10},
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


async def test_mock_confirm_thresholds() -> None:
    client = MockClient()
    strong, _ = await client.confirm_setup({"rule_eval": {"soft_score": 0.9}}, symbol="X")
    marginal, _ = await client.confirm_setup({"rule_eval": {"soft_score": 0.6}}, symbol="X")
    weak, _ = await client.confirm_setup({"rule_eval": {"soft_score": 0.2}}, symbol="X")
    assert strong.action == "CONFIRM"
    assert marginal.action == "REDUCE_SIZE" and marginal.size_multiplier == 0.5
    assert weak.action == "WAIT"
