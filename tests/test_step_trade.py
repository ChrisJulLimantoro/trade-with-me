"""Tests for the scale-out / breakeven / trailing exit stepper (step_trade)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ats.execution.reconcile import TradeState, breakeven_stop, step_trade
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


def test_liquidation_closes_when_stop_is_far() -> None:
    # Stop far below (50); a liq backstop at 80. A bar diving to 78 misses the stop but
    # hits liq → closed at the liq price with reason "liquidation".
    trade = {"direction": "long", "entry_price": 100.0, "stop_loss": 50.0, "take_profit": [120.0]}
    step = _step(trade, _c(101, 78, 79), TradeState(), liq_price=80.0)
    assert step.closed
    assert step.exit_result.exit_reason == "liquidation"
    assert step.exit_result.exit_price == pytest.approx(80.0)


def test_stop_wins_when_nearer_than_liquidation() -> None:
    # Normal case: stop (95) is nearer than liq (80); a bar to 79 trips the stop first.
    step = _step(LONG, _c(101, 79, 80), TradeState(), liq_price=80.0)
    assert step.closed
    assert step.exit_result.exit_reason == "sl"
    assert step.exit_result.exit_price == pytest.approx(95.0)


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


def test_unprotected_flat_trade_closes_at_expiry() -> None:
    # A flat/losing, unprotected trade is still time-stopped (close 99.5 < entry 100).
    step = _step(LONG, _c(101, 99, 99.5, ts=T0), TradeState(), expires_at=T0)
    assert step.closed and step.exit_result.exit_reason == "expiry"


def test_in_profit_trade_survives_expiry() -> None:
    # Fix A: a green but not-yet-breakeven trade is NOT cut by the time-stop. Close 105 >
    # entry 100, no sl/tp hit (tp1=110), so it keeps running past expiry.
    step = _step(LONG, _c(106, 101, 105, ts=T0), TradeState(), expires_at=T0)
    assert not step.closed


def test_early_breakeven_arm_without_scale_out() -> None:
    # Fix B: favorable excursion of 7 pts >= 1x ATR(=5) arms breakeven without scaling out.
    step = _step(LONG, _c(107, 100, 106), TradeState(), breakeven_arm_atr=1.0, atr=5.0)
    assert not step.closed
    assert step.partial is None
    assert step.state.breakeven is True
    assert step.state.working_stop == pytest.approx(100.0)  # entry
    assert step.state.remaining_frac == pytest.approx(1.0)  # nothing banked
    assert step.state.realized_pnl_pct == pytest.approx(0.0)
    assert step.notes  # carries an "armed breakeven early" note


def test_breakeven_stop_covers_costs() -> None:
    # With no costs the breakeven stop is exactly entry; with costs it shifts past entry so
    # a stop-out nets ~flat instead of a fee-only loss.
    assert breakeven_stop("long", 100.0, 0.0) == pytest.approx(100.0)
    assert breakeven_stop("long", 100.0, 10.0) == pytest.approx(100.0 * 1.002)
    assert breakeven_stop("short", 100.0, 10.0) == pytest.approx(100.0 * 0.998)


def test_early_arm_blocked_by_profit_floor() -> None:
    # A favorable excursion that clears the ATR threshold but NOT the cost-based profit
    # floor must not arm breakeven (prevents fee-only scratch exits).
    # floor = entry * 2*cost_bps/1e4 * mult = 100 * 0.002 * 5 = 1.0 ; ATR thresh = 0.1*5 = 0.5
    step = _step(
        LONG, _c(100.6, 100, 100.4), TradeState(),
        breakeven_arm_atr=0.1, atr=5.0, cost_bps=10.0, breakeven_arm_cost_mult=5.0,
    )
    assert step.state.breakeven is False  # excursion 0.6 < profit floor 1.0


def test_review_on_expiry_defers_instead_of_closing() -> None:
    # An unprotected flat/losing trade at expiry returns expiry_due (for caller review)
    # rather than closing here.
    step = _step(LONG, _c(101, 99, 99.5, ts=T0), TradeState(), expires_at=T0,
                 review_on_expiry=True)
    assert not step.closed
    assert step.expiry_due is True


def test_costs_net_full_close() -> None:
    # cost_bps=10 → round-trip cost 2*10bps = 0.002 deducted from a full-position close.
    step = _step(LONG, _c(101, 94, 96), TradeState(), cost_bps=10.0)
    assert step.closed and step.exit_result.exit_reason == "sl"
    assert step.exit_result.pnl_pct == pytest.approx(-0.05 - 0.002)


def test_costs_net_scaled_close_total_two_legs() -> None:
    # Partial at tp1 then final at tp2, with costs. Each leg nets its share; total cost
    # across both legs == 2 * cost_bps (entry + exit on the whole position).
    after_tp1 = _step(LONG, _c(111, 99, 109), TradeState(), cost_bps=10.0).state
    assert after_tp1.realized_pnl_pct == pytest.approx(0.5 * 0.10 - 0.5 * 0.002)
    step = _step(LONG, _c(121, 105, 119), after_tp1, cost_bps=10.0)
    assert step.closed and step.exit_result.exit_reason == "tp"
    gross = 0.5 * 0.10 + 0.5 * 0.20
    assert step.exit_result.pnl_pct == pytest.approx(gross - 0.002)


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
