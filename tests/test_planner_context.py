from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ats import config
from ats.engine import state
from ats.learning.retrieval import retrieve_memory_summary
from ats.planning.context import (
    _htf_momentum,
    _htf_trend_strength,
    build_exhaustion_context,
    build_structure_context,
    build_volume_context,
    preferred_direction,
)


def _htf(tf: str, **feats) -> dict:
    return {tf: {"features": feats}}


def test_htf_trend_strength_is_normalized_ema_separation() -> None:
    # |ema_50 - ema_200| / ema_200 on the 4h chart.
    htf = _htf("4h", ema_50=110.0, ema_200=100.0)
    assert _htf_trend_strength(htf) == pytest.approx(0.10)
    # Flat/coiled stack → ~0 (would be gated by the swing trend-strength floor).
    assert _htf_trend_strength(_htf("4h", ema_50=100.5, ema_200=100.0)) == pytest.approx(0.005)


def test_htf_trend_strength_prefers_4h_then_falls_back_to_1h() -> None:
    both = {"4h": {"features": {"ema_50": 130.0, "ema_200": 100.0}},
            "1h": {"features": {"ema_50": 105.0, "ema_200": 100.0}}}
    assert _htf_trend_strength(both) == pytest.approx(0.30)  # 4h wins
    assert _htf_trend_strength(_htf("1h", ema_50=105.0, ema_200=100.0)) == pytest.approx(0.05)
    assert _htf_trend_strength(_htf("4h", ema_50=None, ema_200=None)) is None


def test_htf_momentum_is_recentred_composite() -> None:
    # momentum_composite in [0,1]; recentred to [-0.5, +0.5] (sign = drive direction).
    assert _htf_momentum(_htf("4h", momentum_composite=0.8)) == pytest.approx(0.30)
    assert _htf_momentum(_htf("4h", momentum_composite=0.2)) == pytest.approx(-0.30)
    assert _htf_momentum(_htf("4h", momentum_composite=0.5)) == pytest.approx(0.0)
    assert _htf_momentum({"4h": {"features": {}}}) is None
from ats.planning.create_plan import build_envelope


def _candle(i: int, *, high: float, low: float, close: float, volume: float = 100.0) -> dict:
    return {
        "open_time": datetime(2026, 1, 1) + timedelta(minutes=15 * i),
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "taker_buy_vol": volume * 0.4,
    }


def test_structure_context_finds_range_pivots_and_atr_distances() -> None:
    candles = [
        _candle(0, high=100, low=90, close=95),
        _candle(1, high=102, low=92, close=98),
        _candle(2, high=101, low=91, close=96),
        _candle(3, high=105, low=95, close=103),
        _candle(4, high=104, low=96, close=101),
        _candle(5, high=103, low=98, close=100),
        _candle(6, high=106, low=99, close=103),
    ]

    out = build_structure_context(candles, {"price": 103.0, "atr_14": 2.0})

    assert out["price_location"] == "upper_range"
    assert out["range_high"] == 106
    assert out["range_low"] == 90
    assert out["range_mid"] == 98
    assert out["nearest_resistance"] == 105
    assert out["nearest_support"] == 90
    assert out["distance_to_resistance_atr"] == 1
    assert out["distance_to_support_atr"] == 6.5
    assert out["last_swing_high"] == 105


def test_exhaustion_context_marks_oversold_bear_squeeze_risk() -> None:
    rows = [
        {"close": 86 + i, "ema_20": 100, "ema_50": 110, "rsi_14": r, "momentum_composite": m}
        for i, (r, m) in enumerate([(25, -0.2), (27, -0.1), (29, -0.05), (31, 0.0), (34, 0.05)])
    ]
    feature = {**rows[-1], "price": 90, "atr_14": 5}
    htf = {"4h": {"features": {"rsi_14": 25}}}

    out = build_exhaustion_context(rows, feature, htf)

    assert out["htf_rsi_state"] == "4h_oversold"
    assert out["base_rsi_slope"] == "rising"
    assert out["momentum_slope"] == "rising"
    assert out["distance_from_ema20_atr"] == -2
    assert out["squeeze_risk"] == "high"


def test_exhaustion_context_marks_overbought_long_squeeze_risk() -> None:
    rows = [
        {"close": 114 - i, "ema_20": 100, "ema_50": 96, "rsi_14": r, "momentum_composite": m}
        for i, (r, m) in enumerate([(75, 0.2), (73, 0.1), (71, 0.05), (69, 0.0), (65, -0.05)])
    ]
    feature = {**rows[-1], "price": 110, "atr_14": 5}
    htf = {"4h": {"features": {"rsi_14": 75}}}

    out = build_exhaustion_context(rows, feature, htf)

    assert out["htf_rsi_state"] == "4h_overbought"
    assert out["base_rsi_slope"] == "falling"
    assert out["momentum_slope"] == "falling"
    assert out["distance_from_ema20_atr"] == 2
    assert out["squeeze_risk"] == "high"


def test_exhaustion_context_neutral_when_not_extended() -> None:
    rows = [
        {
            "close": 100 + i * 0.1,
            "ema_20": 100,
            "ema_50": 100,
            "rsi_14": 50,
            "momentum_composite": 0.5,
        }
        for i in range(5)
    ]
    feature = {**rows[-1], "price": 100.4, "atr_14": 5}

    out = build_exhaustion_context(rows, feature, {"4h": {"features": {"rsi_14": 50}}})

    assert out["htf_rsi_state"] == "neutral"
    assert out["squeeze_risk"] == "normal"


def test_volume_context_uses_relative_volume_taker_ratio_and_cvd_agreement() -> None:
    candles = [
        _candle(i, high=100 + i, low=99 + i, close=100 + i, volume=100)
        for i in range(20)
    ]
    candles.append(_candle(20, high=121, low=119, close=120, volume=200))

    out = build_volume_context(candles, {"cvd_slope_10": -1.0, "vol_zscore_20": 0.5})

    assert out["relative_volume_20"] == 2
    assert out["taker_buy_ratio"] == 0.4
    assert out["cvd_agrees_with_price"] is False
    assert out["breakout_volume_quality"] == "weak"


def test_volume_context_handles_missing_taker_buy_vol() -> None:
    candles = [{"close": 100 + i, "volume": 100, "high": 101 + i, "low": 99 + i} for i in range(21)]

    out = build_volume_context(candles, {"cvd_slope_10": 1.0, "vol_zscore_20": 2.0})

    assert out["taker_buy_ratio"] is None
    assert out["cvd_agrees_with_price"] is True
    assert out["breakout_volume_quality"] == "strong"


class _Rows:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict]:
        return self._rows


class _Session:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def execute(self, *args, **kwargs) -> _Rows:  # noqa: ANN002, ANN003
        return _Rows(self._rows)


def _learning(
    category: str,
    outcome: str,
    pnl_pct: float,
    *,
    hypothesis: str = "",
    proposed_adjustment: str = "",
) -> dict:
    return {
        "category": category,
        "hypothesis": hypothesis,
        "proposed_adjustment": proposed_adjustment,
        "outcome": outcome,
        "pnl_pct": pnl_pct,
    }


async def test_memory_summary_aggregates_similar_learning_rows() -> None:
    rows = [
        _learning("clean_win", "win", 0.05, hypothesis="win"),
        _learning("clean_win", "win", 0.02, hypothesis="win"),
        _learning("clean_win", "win", 0.01, hypothesis="win"),
        _learning("clean_win", "win", 0.04, hypothesis="win"),
        _learning(
            "false_breakout",
            "loss",
            -0.03,
            hypothesis="wait for sweep",
            proposed_adjustment="require sweep",
        ),
        _learning("false_breakout", "loss", -0.02, hypothesis="wait for sweep"),
        _learning("false_breakout", "loss", -0.01, hypothesis="wait for sweep"),
        _learning("false_breakout", "loss", -0.04, hypothesis="wait for sweep"),
        _learning("clean_loss", "loss", -0.01, hypothesis="bad"),
        _learning("clean_loss", "loss", -0.02, hypothesis="bad"),
    ]

    out = await retrieve_memory_summary(_Session(rows), [0.5] * 12)

    assert out["similar_count"] == 10
    assert out["win_rate"] == 0.4
    assert round(out["avg_pnl_pct"], 3) == -0.001
    assert out["top_failure"] == "false_breakout"
    assert out["lesson"] == "require sweep"
    assert out["confidence"] == "normal"


async def test_memory_summary_low_confidence_when_sample_is_sparse() -> None:
    out = await retrieve_memory_summary(
        _Session([_learning("false_breakout", "loss", -0.1, hypothesis="x")]),
        [0.5] * 12,
    )

    assert out["similar_count"] == 1
    assert out["confidence"] == "low"
    assert out["top_failure"] is None
    assert out["lesson"] is None


@pytest.fixture
def _envelope_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.settings.plan, "context_timeframes", ["1h"])
    monkeypatch.setattr(config.settings, "memory_enabled", False)

    async def latest_regime(session, *, before_ts=None):  # noqa: ANN001
        return {"regime_cell": "bear-low", "trend": "bear"}

    async def recent_candles(session, symbol, tf, before_ts, *, n=50):  # noqa: ANN001
        return [_candle(i, high=100 + i, low=90 + i, close=95 + i) for i in range(min(n, 20))]

    async def open_positions(session, *, symbol=None, run_id=None):  # noqa: ANN001
        return []

    async def latest_feature_row(session, symbol, tf, *, as_of=None):  # noqa: ANN001
        return {"open_time": as_of, "price": 110, "close": 110, "rsi_14": 55}

    async def recent_feature_rows(session, symbol, tf, before_ts, *, n=50):  # noqa: ANN001
        return [
            {
                "close": 100 + i,
                "price": 100 + i,
                "ema_20": 100,
                "rsi_14": 50 + i,
                "momentum_composite": 0.4 + i * 0.01,
            }
            for i in range(5)
        ]

    monkeypatch.setattr(state, "latest_regime", latest_regime)
    monkeypatch.setattr(state, "recent_candles", recent_candles)
    monkeypatch.setattr(state, "open_positions", open_positions)
    monkeypatch.setattr(state, "latest_feature_row", latest_feature_row)
    monkeypatch.setattr(state, "recent_feature_rows", recent_feature_rows)


async def test_build_envelope_includes_planner_context_and_prior_lessons(_envelope_state) -> None:
    env = await build_envelope(
        None,
        "BTCUSDT",
        "15m",
        {"price": 110, "close": 110, "atr_14": 5, "rsi_14": 60},
        as_of=datetime(2026, 1, 1, 12, 0),
    )

    assert env["prior_lessons"] == []
    assert set(env["planner_context"]) == {
        "structure",
        "exhaustion",
        "volume_context",
        "memory_summary",
    }
    assert env["planner_context"]["memory_summary"]["confidence"] == "low"
    assert "1h" in env["higher_timeframes"]


# --- preferred_direction (#4) ---------------------------------------------------------


def _exh(htf="neutral", rsi="flat", mom="flat"):
    return {"htf_rsi_state": htf, "base_rsi_slope": rsi, "momentum_slope": mom}


def test_preferred_direction_bear_trend_is_short() -> None:
    assert preferred_direction("bear-low", _exh(), {}) == "short"


def test_preferred_direction_bull_trend_is_long() -> None:
    assert preferred_direction("bull-high", _exh(), {}) == "long"


def test_preferred_direction_bear_with_htf_oversold_flips_long() -> None:
    exh = _exh(htf="4h_oversold", rsi="rising", mom="rising")
    assert preferred_direction("bear-low", exh, {}) == "long"


def test_preferred_direction_bull_with_htf_overbought_flips_short() -> None:
    exh = _exh(htf="4h_overbought", rsi="falling", mom="falling")
    assert preferred_direction("bull-high", exh, {}) == "short"


def test_preferred_direction_side_fades_range_extremes() -> None:
    assert preferred_direction("side-low", _exh(), {"price_location": "upper_range"}) == "short"
    assert preferred_direction("side-low", _exh(), {"price_location": "lower_range"}) == "long"


def test_preferred_direction_side_mid_range_is_none() -> None:
    assert preferred_direction("side-low", _exh(), {"price_location": "mid_range"}) is None


def test_preferred_direction_unknown_regime_is_none() -> None:
    assert preferred_direction(None, _exh(), {}) is None
