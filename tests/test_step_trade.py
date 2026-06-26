"""Tests for the scale-out / breakeven / trailing exit stepper (step_trade)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ats.execution.reconcile import TradeState, _trail_anchor, breakeven_stop, step_trade
from ats.risk.manager import regime_allows

T0 = datetime(2026, 1, 1, tzinfo=UTC)
FAR = T0 + timedelta(days=10)

# Two-target long: TP1=110, TP2=120, stop=95, entry=100.
LONG = {"direction": "long", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": [110.0, 120.0]}
SHORT = {
    "direction": "short",
    "entry_price": 100.0,
    "stop_loss": 105.0,
    "take_profit": [90.0, 80.0],
}


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


def test_range_mode_banks_larger_fraction_at_tp1() -> None:
    step = _step(LONG, _c(111, 99, 109), TradeState(), scale_out_frac=0.75)

    assert step.partial is not None
    assert step.partial.frac == pytest.approx(0.75)
    assert step.state.remaining_frac == pytest.approx(0.25)
    assert step.state.realized_pnl_pct == pytest.approx(0.75 * 0.10)


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


# --- Conditional loser-cut (SPEC3-LOOP / Iter 3) -------------------------------------------

# atr=10, loss_cut_atr_mult=0.4 → tightened stop = entry ∓ 4.0 (long: 96, tighter than full 95).
PAST = T0 - timedelta(hours=1)
_LC = {"atr": 10.0, "loss_cut_atr_mult": 0.4}


def test_loss_cut_ratchets_stalled_red_trade_stop_tighter() -> None:
    # Still-red (close 98 < 100), unprotected, bar past loss_cut_at → stop tightens 95 → 96,
    # but the bar holds (low 97 > 96) so it doesn't exit yet.
    step = _step(LONG, _c(99, 97, 98), TradeState(), loss_cut_at=PAST, **_LC)
    assert not step.closed
    assert step.state.working_stop == pytest.approx(96.0)


def test_loss_cut_capped_loss_is_smaller_than_full_stop() -> None:
    state = _step(LONG, _c(99, 97, 98), TradeState(), loss_cut_at=PAST, **_LC).state
    # next bar dips to the tightened stop: exits at 96 (−4%), not the full 95 (−5%).
    step = _step(LONG, _c(98, 95.5, 96), state, loss_cut_at=PAST, **_LC)
    assert step.closed and step.exit_result.exit_reason == "sl"
    assert step.exit_result.exit_price == pytest.approx(96.0)


def test_loss_cut_inert_before_threshold() -> None:
    step = _step(LONG, _c(99, 97, 98), TradeState(), loss_cut_at=FAR, **_LC)
    assert step.state.working_stop is None  # untouched; full stop still governs


def test_loss_cut_skips_trade_in_profit() -> None:
    # Green bar (close 101 > entry): the winner-side machine owns it, loss-cut must not fire.
    step = _step(LONG, _c(102, 99, 101), TradeState(), loss_cut_at=PAST, **_LC)
    assert step.state.working_stop is None


def test_loss_cut_only_tightens_never_widens() -> None:
    # A working stop already tighter than the loss-cut level (97 > 96) is left alone.
    state = TradeState(working_stop=97.0)
    step = _step(LONG, _c(99, 97.5, 98), state, loss_cut_at=PAST, **_LC)
    assert step.state.working_stop == pytest.approx(97.0)


def test_loss_cut_disabled_by_default_zero_mult() -> None:
    step = _step(LONG, _c(99, 97, 98), TradeState(), loss_cut_at=PAST, atr=10.0)
    assert step.state.working_stop is None  # loss_cut_atr_mult defaults to 0.0 → inert


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
    step = _step(
        LONG,
        _c(107, 100, 106),
        TradeState(),
        early_stop_mode="breakeven",
        breakeven_arm_atr=1.0,
        atr=5.0,
    )
    assert not step.closed
    assert step.partial is None
    assert step.state.breakeven is True
    assert step.state.working_stop == pytest.approx(100.0)  # entry
    assert step.state.remaining_frac == pytest.approx(1.0)  # nothing banked
    assert step.state.realized_pnl_pct == pytest.approx(0.0)
    assert step.notes  # carries an "armed breakeven early" note


def test_range_mode_does_not_early_arm_before_tp1() -> None:
    step = _step(
        LONG,
        _c(107, 100, 106),
        TradeState(),
        early_stop_mode="breakeven",
        breakeven_arm_atr=1.0,
        atr=5.0,
        breakeven_requires_tp1=True,
    )

    assert not step.closed
    assert step.partial is None
    assert step.state.breakeven is False
    assert step.state.working_stop is None


def test_early_trail_arms_without_breakeven() -> None:
    step = _step(
        LONG,
        _c(107, 100, 106),
        TradeState(),
        early_stop_mode="trail",
        early_trail_arm_atr=1.0,
        early_trail_atr_mult=2.0,
        atr=5.0,
    )

    assert not step.closed
    assert step.state.trail_armed is True
    assert step.state.breakeven is False
    assert step.state.working_stop == pytest.approx(96.0)
    assert any("early_trail armed" in note for note in step.notes)


def test_early_stop_off_does_not_arm_protection() -> None:
    step = _step(
        LONG,
        _c(107, 100, 106),
        TradeState(),
        early_stop_mode="off",
        early_trail_arm_atr=1.0,
        early_trail_atr_mult=2.0,
        breakeven_arm_atr=1.0,
        atr=5.0,
    )

    assert step.state.trail_armed is False
    assert step.state.breakeven is False
    assert step.state.working_stop is None


def test_early_trail_never_loosens_original_stop() -> None:
    step = _step(
        LONG,
        _c(107, 100, 106),
        TradeState(),
        early_stop_mode="trail",
        early_trail_arm_atr=1.0,
        early_trail_atr_mult=3.0,
        atr=5.0,
    )

    assert step.state.trail_armed is True
    assert step.state.working_stop == pytest.approx(95.0)


def test_short_early_trail_formula() -> None:
    step = _step(
        SHORT,
        _c(100, 93, 94),
        TradeState(),
        early_stop_mode="trail",
        early_trail_arm_atr=1.0,
        early_trail_atr_mult=2.0,
        atr=5.0,
    )

    assert step.state.trail_armed is True
    assert step.state.breakeven is False
    assert step.state.working_stop == pytest.approx(104.0)


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
        early_stop_mode="breakeven",
        breakeven_arm_atr=0.1,
        atr=5.0,
        cost_bps=10.0,
        breakeven_arm_cost_mult=5.0,
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


# --- chandelier trail anchor ------------------------------------------------------------


def test_trail_anchor_resolver() -> None:
    # close mode always returns the bar close; chandelier returns the high-water extreme
    # once seeded and falls back to the close until then.
    assert _trail_anchor("close", "long", 100.0, 116.0) == pytest.approx(100.0)
    assert _trail_anchor("chandelier", "long", 100.0, 116.0) == pytest.approx(116.0)
    assert _trail_anchor("chandelier", "short", 100.0, 84.0) == pytest.approx(84.0)
    assert _trail_anchor("chandelier", "long", 100.0, None) == pytest.approx(100.0)


def test_chandelier_runner_trails_off_high_water_mark() -> None:
    # TP1 fills (high 111 ≥ 110): scale out, stop→breakeven(100), high-water = 111.
    after_tp1 = _step(LONG, _c(111, 99, 109), TradeState()).state
    assert after_tp1.extreme_favorable_px == pytest.approx(111.0)
    # Runner pushes to a 116 high but closes off it at 112. Chandelier anchors off the 116
    # high: stop -> 116 - 1*ATR(5) = 111.
    trailed = _step(
        LONG, _c(116, 108, 112), after_tp1, trail_atr_mult=1.0, trail_mode="chandelier", atr=5.0
    )
    assert trailed.state.extreme_favorable_px == pytest.approx(116.0)
    assert trailed.state.working_stop == pytest.approx(111.0)
    assert any("trail stop" in note and "chandelier" in note for note in trailed.notes)
    # Close-anchoring on the SAME bar only reaches 112 - 5 = 107 — looser by design.
    close_anchored = _step(
        LONG, _c(116, 108, 112), after_tp1, trail_atr_mult=1.0, trail_mode="close", atr=5.0
    )
    assert close_anchored.state.working_stop == pytest.approx(107.0)
    # A later inside bar (no new high) must NOT pull the chandelier stop back down.
    held = _step(
        LONG, _c(114, 109, 110), trailed.state, trail_atr_mult=1.0, trail_mode="chandelier", atr=5.0
    )
    assert held.state.working_stop == pytest.approx(111.0)


def test_chandelier_runner_short_mirror() -> None:
    # TP1 fills (low 89 ≤ 90): scale out, stop→breakeven(100), low-water = 89.
    after_tp1 = _step(SHORT, _c(100, 89, 91), TradeState()).state
    assert after_tp1.extreme_favorable_px == pytest.approx(89.0)
    # Runner pushes to an 84 low: chandelier anchors off it -> stop = 84 + 1*ATR(5) = 89.
    trailed = _step(
        SHORT, _c(92, 84, 88), after_tp1, trail_atr_mult=1.0, trail_mode="chandelier", atr=5.0
    )
    assert trailed.state.extreme_favorable_px == pytest.approx(84.0)
    assert trailed.state.working_stop == pytest.approx(89.0)
    # Close-anchoring would only reach 88 + 5 = 93 — looser.
    close_anchored = _step(
        SHORT, _c(92, 84, 88), after_tp1, trail_atr_mult=1.0, trail_mode="close", atr=5.0
    )
    assert close_anchored.state.working_stop == pytest.approx(93.0)


def test_chandelier_early_trail_before_tp1() -> None:
    # Pre-TP1 trail armed (excursion 7 ≥ 1*ATR(5)); chandelier anchors off the 107 high:
    # stop -> 107 - 2*ATR(5) = 97, vs close-anchoring which would stay pinned at the base 95.
    step = _step(
        LONG,
        _c(107, 100, 103),
        TradeState(),
        early_stop_mode="trail",
        early_trail_arm_atr=1.0,
        early_trail_atr_mult=2.0,
        trail_mode="chandelier",
        atr=5.0,
    )
    assert step.state.trail_armed is True
    assert step.state.working_stop == pytest.approx(97.0)
    assert any("early_trail stop" in note and "chandelier" in note for note in step.notes)


def test_trade_state_metadata_round_trips_extreme_and_tolerates_missing() -> None:
    # New field survives a metadata round-trip...
    st = TradeState(extreme_favorable_px=116.0)
    assert TradeState.from_metadata(st.to_metadata()).extreme_favorable_px == pytest.approx(116.0)
    # ...and trades opened before this change (no key) deserialize to None, not a crash.
    assert TradeState.from_metadata({"remaining_frac": 1.0}).extreme_favorable_px is None


def test_range_mode_does_not_trail_before_tp1() -> None:
    pre_tp1_be = TradeState(working_stop=100.0, breakeven=True, tp_index=0)

    step = _step(
        LONG,
        _c(113, 108, 112),
        pre_tp1_be,
        trail_atr_mult=1.0,
        atr=5.0,
        trail_after_tp1_only=True,
    )

    assert not step.closed
    assert step.state.working_stop == pytest.approx(100.0)


def test_range_mode_trails_after_tp1() -> None:
    after_tp1 = _step(LONG, _c(111, 99, 109), TradeState()).state

    step = _step(
        LONG,
        _c(113, 108, 112),
        after_tp1,
        trail_atr_mult=1.0,
        atr=5.0,
        trail_after_tp1_only=True,
    )

    assert not step.closed
    assert step.state.working_stop == pytest.approx(107.0)


def test_cost_adjusted_breakeven_stop_reason_is_breakeven() -> None:
    state = TradeState(
        working_stop=breakeven_stop("long", 100.0, 10.0),
        breakeven=True,
    )

    step = _step(LONG, _c(101, 100.1, 100.1), state, cost_bps=10.0)

    assert step.closed
    assert step.exit_result.exit_reason == "breakeven"
    assert step.exit_result.pnl_pct == pytest.approx(0.0)


def test_pre_tp1_trail_below_cost_breakeven_is_sl() -> None:
    state = TradeState(working_stop=99.0, trail_armed=True)

    step = _step(LONG, _c(100, 98.5, 98.8), state, cost_bps=10.0)

    assert step.closed
    assert step.exit_result.exit_reason == "sl"


def test_pre_tp1_trail_beyond_cost_breakeven_is_trail() -> None:
    state = TradeState(working_stop=103.0, trail_armed=True)

    step = _step(LONG, _c(104, 102.5, 102.8), state, cost_bps=10.0)

    assert step.closed
    assert step.exit_result.exit_reason == "trail"


def test_short_partial_scale_out() -> None:
    step = _step(SHORT, _c(101, 89, 91), TradeState())
    assert step.partial is not None and step.state.breakeven
    assert step.state.working_stop == pytest.approx(100.0)
    assert step.state.realized_pnl_pct == pytest.approx(0.5 * 0.10)


def test_metadata_roundtrip() -> None:
    st = TradeState(remaining_frac=0.5, realized_pnl_pct=0.05, working_stop=100.0,
                    tp_index=1, breakeven=True, trail_armed=True)
    assert TradeState.from_metadata(st.to_metadata()) == st


def test_regime_gate() -> None:
    assert regime_allows("bull-low", "long") is True
    assert regime_allows("bull-low", "short") is False
    assert regime_allows("bear-low", "short") is True
    assert regime_allows("bear-low", "long") is False
    assert regime_allows("side-low", "long") is True
    assert regime_allows("side-high", "short") is True
    assert regime_allows(None, "long") is True


def test_regime_gate_counter_trend_on_htf_exhaustion() -> None:
    # Trend direction is always allowed regardless of HTF state.
    assert regime_allows("bear-low", "short", htf_rsi_state="4h_oversold") is True
    assert regime_allows("bull-low", "long", htf_rsi_state="4h_overbought") is True
    # Counter-trend opens only when the HTF is exhausted the matching way.
    assert regime_allows("bear-low", "long", htf_rsi_state="4h_oversold") is True
    assert regime_allows("bull-low", "short", htf_rsi_state="1h_overbought") is True
    # A neutral / wrong-side HTF state keeps the strict trend-only gate.
    assert regime_allows("bear-low", "long", htf_rsi_state="neutral") is False
    assert regime_allows("bear-low", "long", htf_rsi_state="4h_overbought") is False
    assert regime_allows("bull-low", "short", htf_rsi_state="neutral") is False
    # Sideways still allows both; missing state is the strict default.
    assert regime_allows("side-low", "long", htf_rsi_state="neutral") is True
    assert regime_allows("bear-low", "long") is False


# --- close-confirmed base stop on the intrabar cadence (defect 0.2) --------------------


def test_stop_on_close_rides_through_noise_wick() -> None:
    # Acceptance (0.2): on the finer-tf walk a sub-bar wicks through the base stop (low 94 <
    # stop 95) but closes back inside the thesis (96). With close-confirm on it is treated as
    # noise — the trade does NOT stop out and a note records the ignored wick.
    step = _step(LONG, _c(101, 94, 96), TradeState(), stop_on_close=True)
    assert not step.closed
    assert any("wick ignored" in n for n in step.notes)


def test_stop_on_close_exits_when_close_confirms_break() -> None:
    # A sub-bar that actually CLOSES beyond the stop (94.5 < 95) is a real break → stop out
    # at the stop price, even with close-confirm enabled.
    step = _step(LONG, _c(101, 94, 94.5), TradeState(), stop_on_close=True)
    assert step.closed and step.exit_result.exit_reason == "sl"
    assert step.exit_result.exit_price == pytest.approx(95.0)


def test_stop_on_close_off_keeps_intrabar_wick_stop() -> None:
    # Default (single decision-bar path): the original intrabar-wick stop still fills, so the
    # behaviour is unchanged where there is no finer-tf resolution to confirm a close.
    step = _step(LONG, _c(101, 94, 96), TradeState())
    assert step.closed and step.exit_result.exit_reason == "sl"


def test_stop_on_close_does_not_shield_breakeven_stop() -> None:
    # Close-confirm only shields the UNPROTECTED base stop. A breakeven runner whose wick dips
    # to its breakeven stop (100) but closes above it (101) still stops flat — protective
    # stops only ever lock in gains and must fill on the wick.
    after_tp1 = _step(LONG, _c(111, 99, 109), TradeState()).state  # stop → breakeven(100)
    step = _step(LONG, _c(108, 99, 101), after_tp1, stop_on_close=True)
    assert step.closed and step.exit_result.exit_reason == "breakeven"
