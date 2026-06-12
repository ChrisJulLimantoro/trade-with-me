from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ats import config
from ats.engine import state
from ats.learning.retrieval import retrieve_memory_summary
from ats.planning.context import (
    build_exhaustion_context,
    build_structure_context,
    build_volume_context,
)
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
    monkeypatch.setattr(config.settings, "context_timeframes", ["1h"])
    monkeypatch.setattr(config.settings, "memory_enabled", False)

    async def latest_regime(session, *, before_ts=None):  # noqa: ANN001
        return {"regime_cell": "bear-low", "trend": "bear"}

    async def recent_candles(session, symbol, tf, before_ts, *, n=50):  # noqa: ANN001
        return [_candle(i, high=100 + i, low=90 + i, close=95 + i) for i in range(min(n, 20))]

    async def open_positions(session, *, symbol=None):  # noqa: ANN001
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
