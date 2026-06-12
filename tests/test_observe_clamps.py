"""Tests for the observation agent's risk clamps + the deterministic mock decisions.

The clamps are the safety contract: the LLM observation agent may protect or extend an
open trade, but the engine never lets it widen risk.
"""

from __future__ import annotations

from ats.engine.detector import clamp_extended_tp, clamp_tightened_stop
from ats.llm.mock_data import canned_observation

# --- stop clamp: never widen, never cross price -----------------------------------------

def test_long_stop_only_moves_up() -> None:
    # Proposed stop below the current stop would loosen risk → rejected (stays at cur).
    assert clamp_tightened_stop("long", cur_stop=95.0, new_stop=90.0, price=110.0) == 95.0


def test_long_stop_tightens_toward_price() -> None:
    assert clamp_tightened_stop("long", cur_stop=95.0, new_stop=102.0, price=110.0) == 102.0


def test_long_stop_never_above_price() -> None:
    # A stop above current price would fill instantly — clamp to price.
    assert clamp_tightened_stop("long", cur_stop=95.0, new_stop=115.0, price=110.0) == 110.0


def test_short_stop_only_moves_down() -> None:
    # For shorts the stop sits above price; widening = raising it → rejected.
    assert clamp_tightened_stop("short", cur_stop=105.0, new_stop=110.0, price=90.0) == 105.0


def test_short_stop_tightens_toward_price() -> None:
    assert clamp_tightened_stop("short", cur_stop=105.0, new_stop=98.0, price=90.0) == 98.0


def test_short_stop_never_below_price() -> None:
    assert clamp_tightened_stop("short", cur_stop=105.0, new_stop=85.0, price=90.0) == 90.0


# --- take-profit clamp: extend outward only ---------------------------------------------

def test_long_tp_extends_outward() -> None:
    assert clamp_extended_tp("long", cur_final_tp=120.0, new_tp=130.0) == 130.0


def test_long_tp_never_cut_short() -> None:
    assert clamp_extended_tp("long", cur_final_tp=120.0, new_tp=110.0) == 120.0


def test_short_tp_extends_outward() -> None:
    assert clamp_extended_tp("short", cur_final_tp=80.0, new_tp=70.0) == 70.0


def test_short_tp_never_cut_short() -> None:
    assert clamp_extended_tp("short", cur_final_tp=80.0, new_tp=90.0) == 80.0


# --- canned observation decisions (deterministic mock) ----------------------------------

def _env(direction: str, unrealized: float, macd_hist: float, price: float = 100.0) -> dict:
    return {
        "direction": direction,
        "unrealized_pct": unrealized,
        "current_price": price,
        "entry_price": 95.0,
        "take_profit": [110.0, 120.0],
        "features_now": {"macd_hist": macd_hist},
    }


def test_mock_exits_on_reversal_when_not_in_profit() -> None:
    obs = canned_observation(_env("long", unrealized=-0.002, macd_hist=-0.5))
    assert obs.action == "EXIT_NOW"
    assert obs.confidence >= 0.7  # clears the default exit floor


def test_mock_prefers_explicit_margin_unrealized_alias() -> None:
    env = _env("long", unrealized=0.02, macd_hist=-0.5)
    env["unrealized_margin_pct"] = -0.002

    obs = canned_observation(env)

    assert obs.action == "EXIT_NOW"


def test_mock_exits_stale_flat_trade_without_favorable_progress() -> None:
    env = _env("long", unrealized=0.0005, macd_hist=0.0)
    env["hold"] = {"current_window_progress": 0.8}
    env["progress"] = {"max_favorable_pnl_pct": 0.001, "stale_candidate": True}

    obs = canned_observation(env)

    assert obs.action == "EXIT_NOW"
    assert obs.confidence >= 0.7


def test_mock_tightens_when_gain_and_momentum_fades() -> None:
    obs = canned_observation(_env("long", unrealized=0.02, macd_hist=-0.1))
    assert obs.action == "TIGHTEN_STOP"
    assert obs.new_stop is not None


def test_mock_raises_tp_on_strong_continuation() -> None:
    obs = canned_observation(_env("long", unrealized=0.02, macd_hist=0.5))
    assert obs.action == "RAISE_TP"
    assert obs.new_tp and obs.new_tp[-1] > 120.0


def test_mock_holds_by_default() -> None:
    obs = canned_observation(_env("long", unrealized=0.0, macd_hist=0.05))
    assert obs.action == "HOLD"


def test_mock_is_deterministic() -> None:
    env = _env("short", unrealized=0.02, macd_hist=0.5)
    assert canned_observation(env) == canned_observation(env)
