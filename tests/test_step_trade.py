"""Tests for the scale-out / breakeven / trailing exit stepper (step_trade)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ats.execution.reconcile import TradeState, step_trade
from ats.risk.manager import regime_allows

T0 = datetime(2026, 1, 1, tzinfo=UTC)
FAR = T0 + timedelta(days=10)

# Two-target long: TP1=110, TP2=120, stop=95, entry=100.
LONG = {"direction": "long", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": [110.0, 120.0]}
SHORT = {"direction": "short", "entry_price": 100.0, "stop_loss": 105.0, "take_profit": [90.0, 80.0]}


def _c(high: float, low: float, close: float, ts: datetime = T0) -> dict:
    return {"open_time": ts, "high": high, "low": low, "close": close}


def _step(trade, candle, state, **kw):
    kw.setdefault("expires_at", FAR)
    kw.setdefault("scale_out_frac", 0.5)
    return step_trade(trade, candle, state, **kw)


def test_partial_scale_out_at_tp1_moves_to_breakeven() -> None:
    step = _step(LONG, _c(111, 99, 109), TradeState())
    assert not step.closed
    assert step.partial is not None
    assert step.partial.tp_index == 0
    assert step.state.remaining_frac == pytest.approx(0.5)
    assert step.state.breakeven is True
    assert step.state.working_stop == pytest.approx(100.0)  # entry
    assert step.state.tp_index == 1
    # banked leg pnl: 0.5 of position * +10%
    assert step.state.realized_pnl_pct == pytest.approx(0.5 * 0.10)


def test_runner_closes_at_final_tp_with_summed_pnl() -> None:
    after_tp1 = _step(LONG, _c(111, 99, 109), TradeState()).state
    step = _step(LONG, _c(121, 105, 119), after_tp1)
    assert step.closed
    assert step.exit_result.exit_reason == "tp"
    # 0.5 banked at +10% + 0.5 at +20% = 0.15 total
    assert step.exit_result.pnl_pct == pytest.approx(0.5 * 0.10 + 0.5 * 0.20)


def test_breakeven_runner_stops_flat_not_at_loss() -> None:
    after_tp1 = _step(LONG, _c(111, 99, 109), TradeState()).state
    # price falls back through entry: working stop is breakeven (100), not original 95
    step = _step(LONG, _c(108, 99, 99), after_tp1)
    assert step.closed
    assert step.exit_result.exit_reason == "breakeven"
    assert step.exit_result.exit_price == pytest.approx(100.0)
    # remaining 0.5 exits flat, so total pnl is just the banked TP1 leg
    assert step.exit_result.pnl_pct == pytest.approx(0.5 * 0.10)


def test_first_bar_full_stop_is_a_loss() -> None:
    step = _step(LONG, _c(101, 94, 96), TradeState())
    assert step.closed and step.exit_result.exit_reason == "sl"
    assert step.exit_result.pnl_pct == pytest.approx(-0.05)


def test_breakeven_runner_survives_expiry() -> None:
    after_tp1 = _step(LONG, _c(111, 99, 109), TradeState()).state
    # at/after expiry but protected → keep running, do not close
    step = _step(LONG, _c(112, 101, 111, ts=T0), after_tp1, expires_at=T0)
    assert not step.closed


def test_unprotected_trade_closes_at_expiry() -> None:
    step = _step(LONG, _c(101, 99, 100.5, ts=T0), TradeState(), expires_at=T0)
    assert step.closed and step.exit_result.exit_reason == "expiry"


def test_trailing_stop_locks_in_profit() -> None:
    after_tp1 = _step(LONG, _c(111, 99, 109), TradeState()).state
    # runner trails by 1x ATR(=5) off the close(112): working stop -> 107
    trailed = _step(LONG, _c(113, 108, 112), after_tp1, trail_atr_mult=1.0, atr=5.0)
    assert not trailed.closed
    assert trailed.state.working_stop == pytest.approx(107.0)
    # next bar dips to 106 → stop out at the trailed 107 (a profit), reason "trail"
    out = _step(LONG, _c(110, 106, 107), trailed.state, trail_atr_mult=1.0, atr=5.0)
    assert out.closed and out.exit_result.exit_reason == "trail"
    assert out.exit_result.pnl_pct == pytest.approx(0.5 * 0.10 + 0.5 * 0.07)


def test_short_partial_scale_out() -> None:
    step = _step(SHORT, _c(101, 89, 91), TradeState())
    assert step.partial is not None and step.state.breakeven
    assert step.state.working_stop == pytest.approx(100.0)
    assert step.state.realized_pnl_pct == pytest.approx(0.5 * 0.10)


def test_metadata_roundtrip() -> None:
    st = TradeState(remaining_frac=0.5, realized_pnl_pct=0.05, working_stop=100.0,
                    tp_index=1, breakeven=True)
    assert TradeState.from_metadata(st.to_metadata()) == st


def test_regime_gate() -> None:
    assert regime_allows("bull-low", "long") is True
    assert regime_allows("bull-low", "short") is False
    assert regime_allows("bear-low", "short") is True
    assert regime_allows("bear-low", "long") is False
    assert regime_allows("side-low", "long") is False
    assert regime_allows("side-low", "short") is False
    assert regime_allows(None, "long") is True
