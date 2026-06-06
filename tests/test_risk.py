"""Tests for the deterministic risk manager: reward:risk, risk-based sizing, leverage."""

from __future__ import annotations

import pytest

from ats.risk.manager import assess, reward_risk, size_for_risk

BASE_SETUP = {
    "symbol": "BTCUSDT",
    "direction": "long",
    "stop_loss": 95.0,
    "take_profit": [110.0, 120.0],
    "size_pct": 0.05,  # ignored by risk-based sizing; kept for schema parity
}

EQUITY = 10_000.0


def _assess(setup: dict, **kw: object):
    params: dict = {
        "price": 100,
        "open_positions": [],
        "equity_usd": EQUITY,
        "risk_per_trade_pct": 0.01,
        "max_leverage": 3.0,
        "min_rr": 1.5,
        "atr": None,
        "min_stop_atr_mult": 0.0,
    }
    params.update(kw)
    return assess(setup, **params)


# --- reward:risk ------------------------------------------------------------------------

def test_reward_risk_long() -> None:
    assert reward_risk("long", 100, 95, [110, 120]) == pytest.approx(2.0)


def test_reward_risk_short() -> None:
    assert reward_risk("short", 100, 105, [90, 80]) == pytest.approx(2.0)


def test_reward_risk_zero_when_risk_nonpositive() -> None:
    assert reward_risk("long", 100, 100, [110]) == 0.0


# --- size_for_risk (pure sizing) --------------------------------------------------------

def test_sizing_targets_risk_budget_when_uncapped() -> None:
    # stop 5% away, 1% risk of $10k → $100 at risk; notional = 100/0.05 = $2000.
    s = size_for_risk("long", 100, 95, equity_usd=EQUITY, risk_pct=0.01, max_leverage=3.0)
    assert s.ok
    assert s.risk_usd == pytest.approx(100.0)
    assert s.notional_usd == pytest.approx(2000.0)
    assert s.margin_usd == pytest.approx(2000.0)
    assert s.size_pct == pytest.approx(0.2)
    assert s.leverage == pytest.approx(1.0)
    assert s.liq_price is None  # 1x: no liquidation risk


def test_sizing_capped_by_margin_and_leverage_lowers_realized_risk() -> None:
    # Tight 0.1% stop wants huge notional; 20% margin at 3x caps it to $6k.
    s = size_for_risk("long", 100, 99.9, equity_usd=EQUITY, risk_pct=0.01, max_leverage=3.0)
    assert s.ok
    assert s.margin_usd == pytest.approx(2000.0)
    assert s.leverage == pytest.approx(3.0)
    assert s.notional_usd == pytest.approx(6000.0)
    assert s.risk_usd == pytest.approx(6.0)  # 6000 * 0.001, under the $100 budget
    assert s.liq_price is not None and s.liq_price == pytest.approx(70.0)  # 100*(1-0.9/3)


def test_sizing_rejects_zero_stop_distance() -> None:
    s = size_for_risk("long", 100, 100, equity_usd=EQUITY, risk_pct=0.01, max_leverage=3.0)
    assert not s.ok


def test_sizing_caps_unsafe_leverage_inside_liquidation() -> None:
    # High leverage + wider stop gets capped to the safe leverage instead of using 50x.
    s = size_for_risk("long", 100, 98, equity_usd=EQUITY, risk_pct=0.95, max_leverage=50.0)
    assert s.ok
    assert s.leverage < 50.0
    assert s.liq_price is not None
    assert s.liq_price < 98.0


def test_sizing_short_liq_is_above_entry() -> None:
    s = size_for_risk("short", 100, 100.1, equity_usd=EQUITY, risk_pct=0.01, max_leverage=3.0)
    assert s.ok and s.leverage == pytest.approx(3.0)
    assert s.liq_price is not None and s.liq_price > 100  # short liquidates upward


# --- assess (gates + sizing) ------------------------------------------------------------

def test_approved_sizes_by_risk() -> None:
    d = _assess(BASE_SETUP)
    assert d.approved
    assert d.risk_usd == pytest.approx(100.0)  # 1% of $10k
    assert d.size_pct == pytest.approx(0.2)
    assert d.margin_usd == pytest.approx(2000.0)
    assert d.notional_usd == pytest.approx(2000.0)
    assert d.leverage == pytest.approx(1.0)


def test_rejected_existing_position_same_symbol() -> None:
    d = _assess(BASE_SETUP, open_positions=[{"symbol": "BTCUSDT"}])
    assert d.approved is False and "open position" in d.reasons[0]


def test_other_symbol_position_does_not_block() -> None:
    d = _assess(BASE_SETUP, open_positions=[{"symbol": "ETHUSDT"}])
    assert d.approved is True


def test_rejected_low_reward_risk() -> None:
    setup = {**BASE_SETUP, "take_profit": [101.0]}  # rr = 0.2
    d = _assess(setup)
    assert d.approved is False and "reward:risk" in d.reasons[0]


def test_leverage_capped_at_max() -> None:
    setup = {**BASE_SETUP, "stop_loss": 99.9}  # very tight stop → wants > max_leverage
    d = _assess(setup)
    assert d.approved and d.leverage == pytest.approx(3.0)
    assert d.margin_usd == pytest.approx(EQUITY * 0.20)
    assert d.notional_usd == pytest.approx(EQUITY * 0.20 * 3.0)
    assert d.risk_usd < 100.0  # capped notional → risk below the budget


def test_reduce_size_multiplier_scales_risk_down() -> None:
    full = _assess(BASE_SETUP)
    reduced = _assess(BASE_SETUP, size_multiplier=0.5)
    assert reduced.approved
    assert reduced.risk_usd == pytest.approx(full.risk_usd * 0.5)
    assert reduced.size_pct == pytest.approx(full.size_pct * 0.5)


def test_total_margin_heat_rejects_when_exhausted() -> None:
    d = _assess(
        BASE_SETUP,
        open_positions=[
            {"symbol": "ETHUSDT", "margin_usd": EQUITY * 0.60, "risk_usd": 0.0}
        ],
    )
    assert d.approved is False
    assert "margin exhausted" in d.reasons[0]


def test_portfolio_risk_heat_rejects_when_exhausted() -> None:
    d = _assess(
        BASE_SETUP,
        open_positions=[
            {"symbol": "ETHUSDT", "margin_usd": 0.0, "risk_usd": EQUITY * 0.03}
        ],
    )
    assert d.approved is False
    assert "risk exhausted" in d.reasons[0]


def test_scalper_tight_stop_caps_near_600_notional() -> None:
    s = size_for_risk(
        "short",
        70_991.9,
        71_280.0,
        equity_usd=1000.0,
        risk_pct=0.01,
        max_leverage=3.0,
        max_margin_pct_per_trade=0.20,
    )
    assert s.ok
    assert s.margin_usd == pytest.approx(200.0)
    assert s.notional_usd == pytest.approx(600.0)
    assert s.leverage == pytest.approx(3.0)


def test_risk_never_exceeds_budget() -> None:
    # Across a range of stop distances (all clearing min_rr), realized risk stays <= budget.
    for stop in (94.0, 95.0, 98.0, 99.0, 99.5):
        d = _assess({**BASE_SETUP, "stop_loss": stop})
        assert d.approved
        assert d.risk_usd <= 100.0 + 1e-6


# --- ATR-relative noise-stop filter ---------------------------------------------------------

def test_atr_stop_rejected_when_stop_too_tight() -> None:
    # ATR=10; min_stop_atr_mult=1.5 → need at least 15 pts. Stop at 98 = 2pts away → reject.
    d = _assess(BASE_SETUP, atr=10.0, min_stop_atr_mult=1.5, price=100)
    # BASE_SETUP stop_loss=95 → stop_dist=5pts, ATR=10 → need 15pts → rejected
    assert d.approved is False
    assert "noise-stop" in d.reasons[0]


def test_atr_stop_approved_when_stop_wide_enough() -> None:
    # entry=100, stop=82 → dist=18pts, ATR=10, min_mult=1.5 → need 15pts → 18 > 15 → OK
    # tp=[130] → RR = 30/18 ≈ 1.67 > 1.5 → passes RR check too
    setup = {**BASE_SETUP, "stop_loss": 82.0, "take_profit": [130.0]}
    d = _assess(setup, atr=10.0, min_stop_atr_mult=1.5, price=100)
    assert d.approved


def test_atr_stop_disabled_when_mult_zero() -> None:
    # min_stop_atr_mult=0 disables the check — tight stop still approved if RR is fine
    d = _assess(BASE_SETUP, atr=10.0, min_stop_atr_mult=0.0, price=100)
    assert d.approved  # stop_dist=5 < 1.5*10=15 but mult=0 so check is skipped


def test_atr_stop_disabled_when_atr_none() -> None:
    # atr=None disables the check
    d = _assess(BASE_SETUP, atr=None, min_stop_atr_mult=1.5, price=100)
    assert d.approved
