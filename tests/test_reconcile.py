"""Tests for paper-trade reconciliation (pure exit logic)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ats.execution.reconcile import check_bar_exit, reconcile_trade

T0 = datetime(2026, 1, 1, tzinfo=UTC)
FAR = T0 + timedelta(days=10)
LONG = {"direction": "long", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": [110.0]}
SHORT = {"direction": "short", "entry_price": 100.0, "stop_loss": 105.0, "take_profit": [90.0]}


def _c(high: float, low: float, close: float, ts: datetime = T0) -> dict:
    return {"open_time": ts, "high": high, "low": low, "close": close}


def test_straddle_bar_exits_at_stop_conservative() -> None:
    # bar hits both SL (90<=95) and TP (115>=110) → stop wins
    r = reconcile_trade(LONG, [_c(115, 90, 100)], expires_at=FAR)
    assert r is not None and r.exit_reason == "sl" and r.exit_price == 95.0


def test_pure_take_profit() -> None:
    r = reconcile_trade(LONG, [_c(112, 99, 108)], expires_at=FAR)
    assert r is not None and r.exit_reason == "tp" and r.exit_price == 110.0
    assert r.pnl_pct == pytest.approx(0.10)


def test_pure_stop_loss() -> None:
    r = reconcile_trade(LONG, [_c(101, 94, 96)], expires_at=FAR)
    assert r is not None and r.exit_reason == "sl"
    assert r.pnl_pct == pytest.approx(-0.05)


def test_expiry_exit_at_close() -> None:
    # no SL/TP hit, but bar is at/after expiry → close at bar close
    r = reconcile_trade(LONG, [_c(101, 99, 100.5, ts=T0)], expires_at=T0)
    assert r is not None and r.exit_reason == "expiry" and r.exit_price == 100.5


def test_still_open_returns_none() -> None:
    r = reconcile_trade(LONG, [_c(101, 99, 100)], expires_at=FAR)
    assert r is None


def test_short_take_profit_pnl_positive() -> None:
    r = reconcile_trade(SHORT, [_c(101, 89, 95)], expires_at=FAR)
    assert r is not None and r.exit_reason == "tp"
    assert r.pnl_pct == pytest.approx(0.10)  # short gains when price falls


def test_short_stop_loss_pnl_negative() -> None:
    r = reconcile_trade(SHORT, [_c(106, 99, 104)], expires_at=FAR)
    assert r is not None and r.exit_reason == "sl"
    assert r.pnl_pct == pytest.approx(-0.05)


def test_hard_invalidation_exit() -> None:
    r = check_bar_exit(LONG, _c(101, 99, 100.5), expires_at=FAR, hard_invalidation=True)
    assert r is not None and r.exit_reason == "invalidation" and r.exit_price == 100.5


def test_invalidation_hits_via_reconcile() -> None:
    candle = _c(101, 99, 100.5, ts=T0)
    r = reconcile_trade(LONG, [candle], expires_at=FAR, invalidation_hits=frozenset({T0}))
    assert r is not None and r.exit_reason == "invalidation"


def test_first_exit_wins_across_bars() -> None:
    bars = [_c(101, 99, 100, ts=T0), _c(112, 99, 108, ts=T0 + timedelta(hours=1))]
    r = reconcile_trade(LONG, bars, expires_at=FAR)
    assert r is not None and r.exit_reason == "tp" and r.exit_time == T0 + timedelta(hours=1)
