"""Mean-reversion proposer: range-fade triggers, skips, RR floor, causality + router gating."""

from __future__ import annotations

import dataclasses

from ats.config import settings
from ats.strategy.deterministic import propose_signal
from ats.strategy_profiles import apply_profile
from ats.synthesis.mean_reversion import propose_mean_reversion

# Range over the 20-bar window is [90, 110] (mid 100); atr = 2.0 → range_atr = 10.
_KW = dict(
    range_lookback=20,
    edge_frac=0.15,
    rsi_os=35.0,
    rsi_ob=65.0,
    stop_buffer_atr=0.5,
    min_range_atr=2.0,
    min_rr=1.5,
    fee_bps=5.0,
    slippage_bps=2.0,
)


def _bar(h, lo):
    return {"open": (h + lo) / 2, "high": h, "low": lo, "close": (h + lo) / 2, "volume": 1000.0}


def _window_bars(decision_high=10_000.0, decision_low=0.0):
    """20 range bars (high 110 / low 90) + one wild decision bar that must be EXCLUDED."""
    bars = [_bar(110.0, 90.0) for _ in range(20)]
    bars.append(_bar(decision_high, decision_low))  # recent_ohlcv[-1] — excluded from the range
    return bars


def _feat(close: float, rsi: float) -> dict:
    return {"close": close, "atr_14": 2.0, "rsi_14": rsi, "atr_pct": 0.002}


def test_long_fade_at_bottom_edge() -> None:
    sig = propose_mean_reversion(_feat(92.0, 30.0), _window_bars(), **_KW)
    assert sig is not None and sig.direction == "long"
    assert sig.take_profit == [100.0, 100.0]          # both legs at the range mid
    assert sig.stop_loss < sig.entry_zone[0]           # stop below the band
    assert sig.risk_reward >= 1.5
    assert sig.metadata["strategy"] == "mean_reversion"
    assert sig.metadata["band_low"] == 90.0 and sig.metadata["band_high"] == 110.0


def test_short_fade_at_top_edge() -> None:
    sig = propose_mean_reversion(_feat(108.0, 72.0), _window_bars(), **_KW)
    assert sig is not None and sig.direction == "short"
    assert sig.take_profit == [100.0, 100.0]
    assert sig.stop_loss > sig.entry_zone[1]           # stop above the band


def test_midband_stands_aside() -> None:
    assert propose_mean_reversion(_feat(100.0, 50.0), _window_bars(), **_KW) is None


def test_edge_without_rsi_confirm_stands_aside() -> None:
    # At the bottom edge but RSI not oversold → no fade.
    assert propose_mean_reversion(_feat(92.0, 50.0), _window_bars(), **_KW) is None


def test_narrow_range_skipped() -> None:
    bars = [_bar(100.5, 99.5) for _ in range(20)] + [_bar(100.0, 100.0)]
    assert propose_mean_reversion(_feat(99.6, 30.0), bars, **_KW) is None


def test_rr_floor_rejects() -> None:
    assert propose_mean_reversion(_feat(92.0, 30.0), _window_bars(), **{**_KW, "min_rr": 100.0}) is None


def test_range_excludes_decision_bar_no_lookahead() -> None:
    # The wild last bar (high 10000 / low 0) must NOT widen the band; if it leaked in, band_high
    # would blow up and the bottom-edge long trigger would never fire.
    sig = propose_mean_reversion(_feat(92.0, 30.0), _window_bars(10_000.0, 0.0), **_KW)
    assert sig is not None
    assert sig.metadata["band_high"] == 110.0 and sig.metadata["band_low"] == 90.0


def test_deterministic() -> None:
    a = propose_mean_reversion(_feat(92.0, 30.0), _window_bars(), **_KW)
    b = propose_mean_reversion(_feat(92.0, 30.0), _window_bars(), **_KW)
    assert dataclasses.asdict(a) == dataclasses.asdict(b)


def _envelope(close, rsi, regime_cell="side-low"):
    return {
        "as_of": None,
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "features": _feat(close, rsi),
        "regime": {"regime_cell": regime_cell},
        "recent_ohlcv": _window_bars(),
        "higher_timeframes": {},
        # Trend gate forbids longs in side-* (sideways_block_longs), but MR uses its own set.
        "risk_limits": {"allowed_directions": ["short"], "mr_allowed_directions": ["long", "short"]},
    }


def test_router_fires_in_lowvol_sideways_and_bypasses_trend_block() -> None:
    apply_profile("scalper")
    try:
        sig, _ = propose_signal(_envelope(92.0, 30.0), symbol="BTCUSDT")
        assert sig is not None and sig.direction == "long"
        assert sig.metadata.get("strategy") == "mean_reversion"
    finally:
        apply_profile("baseline")


def test_router_inert_when_disabled() -> None:
    apply_profile("scalper")
    settings.plan.mr_enabled = False
    try:
        sig, _ = propose_signal(_envelope(92.0, 30.0), symbol="BTCUSDT")
        # With MR off, the bar falls through to the trend path (no MR signal returned).
        assert sig is None or sig.metadata.get("strategy") != "mean_reversion"
    finally:
        apply_profile("baseline")


def test_router_respects_mr_regime_gate() -> None:
    apply_profile("scalper")
    try:
        env = _envelope(92.0, 30.0)
        env["risk_limits"]["mr_allowed_directions"] = ["short"]  # long now hard-gated out
        sig, _ = propose_signal(env, symbol="BTCUSDT")
        assert sig is None or sig.metadata.get("strategy") != "mean_reversion"
    finally:
        apply_profile("baseline")
