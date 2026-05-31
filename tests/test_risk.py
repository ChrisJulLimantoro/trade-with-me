"""Tests for the deterministic risk manager."""

from __future__ import annotations

import pytest

from ats.risk.manager import assess, reward_risk

BASE_SETUP = {
    "symbol": "BTCUSDT",
    "direction": "long",
    "stop_loss": 95.0,
    "take_profit": [110.0, 120.0],
    "size_pct": 0.05,
}


def test_reward_risk_long() -> None:
    # entry 100, stop 95 (risk 5), tp1 110 (reward 10) → 2.0
    assert reward_risk("long", 100, 95, [110, 120]) == pytest.approx(2.0)


def test_reward_risk_short() -> None:
    # entry 100, stop 105 (risk 5), tp1 90 (reward 10) → 2.0
    assert reward_risk("short", 100, 105, [90, 80]) == pytest.approx(2.0)


def test_reward_risk_zero_when_risk_nonpositive() -> None:
    assert reward_risk("long", 100, 100, [110]) == 0.0


def test_approved_basic() -> None:
    d = assess(BASE_SETUP, price=100, open_positions=[], max_position_pct=0.10, min_rr=1.5)
    assert d.approved is True and d.size_pct == 0.05


def test_rejected_existing_position_same_symbol() -> None:
    d = assess(
        BASE_SETUP, price=100, open_positions=[{"symbol": "BTCUSDT"}],
        max_position_pct=0.10, min_rr=1.5,
    )
    assert d.approved is False and "open position" in d.reasons[0]


def test_other_symbol_position_does_not_block() -> None:
    d = assess(
        BASE_SETUP, price=100, open_positions=[{"symbol": "ETHUSDT"}],
        max_position_pct=0.10, min_rr=1.5,
    )
    assert d.approved is True


def test_rejected_low_reward_risk() -> None:
    setup = {**BASE_SETUP, "take_profit": [101.0]}  # rr = 0.2
    d = assess(setup, price=100, open_positions=[], max_position_pct=0.10, min_rr=1.5)
    assert d.approved is False and "reward:risk" in d.reasons[0]


def test_size_capped_to_max() -> None:
    setup = {**BASE_SETUP, "size_pct": 0.50}
    d = assess(setup, price=100, open_positions=[], max_position_pct=0.10, min_rr=1.5)
    assert d.approved is True and d.size_pct == 0.10


def test_reduce_size_multiplier_applied() -> None:
    d = assess(
        BASE_SETUP, price=100, open_positions=[], max_position_pct=0.10, min_rr=1.5,
        size_multiplier=0.5,
    )
    assert d.approved is True and d.size_pct == pytest.approx(0.025)
