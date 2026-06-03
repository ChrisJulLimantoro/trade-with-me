"""Higher-timeframe context in build_envelope: the look-ahead guard.

A slower-chart bar with open_time T only CLOSES at T + tf_duration, so at decision time
`as_of` it is fully formed only when T <= as_of - tf_duration. build_envelope must feed the
strategist the most-recent CLOSED htf bar and never the one still forming — otherwise replay
leaks the future. These tests pin that selection without touching the DB.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ats import config
from ats.engine import state
from ats.engine.timeframes import timeframe_to_timedelta
from ats.planning import create_plan

BASE_TF = "15m"
AS_OF = datetime(2026, 6, 3, 13, 15)  # a 15m close, mid-way through the 12:00 4h bar


@pytest.fixture(autouse=True)
def _patch_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the DB readers with an in-memory candle store keyed by timeframe.

    Each tf has bars on its natural grid; the feature reader returns the latest bar whose
    open_time <= the as_of it is called with (mirroring the real SQL filter).
    """
    monkeypatch.setattr(config.settings, "context_timeframes", ["1h", "4h"])
    monkeypatch.setattr(config.settings, "memory_enabled", False)

    def _grid(tf: str, count: int = 20) -> list[datetime]:
        step = timeframe_to_timedelta(tf)
        # anchor on a clean midnight so 1h/4h grids line up
        anchor = datetime(2026, 6, 3, 0, 0)
        return [anchor + step * i for i in range(count)]

    async def fake_latest_feature_row(session, symbol, tf, *, as_of=None):  # noqa: ANN001
        bars = [t for t in _grid(tf) if as_of is None or t <= as_of]
        if not bars:
            return None
        return {"open_time": bars[-1], "rsi_14": 55.0, "close": 100.0, "price": 100.0}

    async def fake_recent_candles(session, symbol, tf, before_ts, *, n=50):  # noqa: ANN001
        bars = [t for t in _grid(tf) if t <= before_ts][-n:]
        return [{"open_time": t, "close": 100.0} for t in bars]

    async def fake_latest_regime(session, *, before_ts=None):  # noqa: ANN001
        return {"regime_cell": "trend_up", "trend": "up"}

    async def fake_open_positions(session, *, symbol=None):  # noqa: ANN001
        return []

    monkeypatch.setattr(state, "latest_feature_row", fake_latest_feature_row)
    monkeypatch.setattr(state, "recent_candles", fake_recent_candles)
    monkeypatch.setattr(state, "latest_regime", fake_latest_regime)
    monkeypatch.setattr(state, "open_positions", fake_open_positions)


async def test_htf_block_present_for_each_context_tf() -> None:
    env = await create_plan.build_envelope(
        None, "BTCUSDT", BASE_TF, {"close": 100.0, "rsi_14": 50.0}, as_of=AS_OF
    )
    assert set(env["higher_timeframes"]) == {"1h", "4h"}


async def test_htf_bar_is_already_closed_no_lookahead() -> None:
    env = await create_plan.build_envelope(
        None, "BTCUSDT", BASE_TF, {"close": 100.0, "rsi_14": 50.0}, as_of=AS_OF
    )
    for tf, block in env["higher_timeframes"].items():
        open_time = block["features"]["open_time"]
        close_time = open_time + timeframe_to_timedelta(tf)
        # The fed bar must have CLOSED at or before the decision time.
        assert close_time <= AS_OF, f"{tf} bar closing {close_time} leaks past {AS_OF}"
    # Concretely: at 13:15 the freshest CLOSED 4h bar opened at 08:00 (closes 12:00),
    # NOT the 12:00 bar (closes 16:00, still forming).
    assert env["higher_timeframes"]["4h"]["features"]["open_time"] == datetime(2026, 6, 3, 8, 0)
    assert env["higher_timeframes"]["1h"]["features"]["open_time"] == datetime(2026, 6, 3, 12, 0)


async def test_base_tf_excluded_from_context() -> None:
    monkey_tf = BASE_TF
    env = await create_plan.build_envelope(
        None, "BTCUSDT", monkey_tf, {"close": 100.0, "rsi_14": 50.0}, as_of=AS_OF
    )
    assert monkey_tf not in env["higher_timeframes"]


async def test_missing_htf_features_skipped_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    async def none_row(session, symbol, tf, *, as_of=None):  # noqa: ANN001
        return None

    monkeypatch.setattr(state, "latest_feature_row", none_row)
    env = await create_plan.build_envelope(
        None, "BTCUSDT", BASE_TF, {"close": 100.0, "rsi_14": 50.0}, as_of=AS_OF
    )
    assert env["higher_timeframes"] == {}
