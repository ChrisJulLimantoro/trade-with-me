"""Tests for the entry-bar reconcile: a trade opened on the decision bar is exit-checked over
the REST of its own bar (the finer sub-candles after the fill), so a same-bar stop-out is
booked now instead of being deferred to the next bar — the optimistic-bias fix.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from ats.config import settings
from ats.engine import orchestrator
from ats.engine.orchestrator import _post_fill_candles, _reconcile_entry_bar
from ats.engine.reports import TickReport

NOW = datetime(2026, 1, 1, 0, 0, 0)


def _c(open_time: datetime, low: float, high: float) -> dict[str, Any]:
    return {"open_time": open_time, "low": low, "high": high, "close": (low + high) / 2}


# --------------------------------------------------------------------------------------
# _post_fill_candles — the pure ordering guard
# --------------------------------------------------------------------------------------


def test_post_fill_returns_candles_after_the_fill_touch() -> None:
    # zone (95,105): candle 0 never touches, candle 1 touches (the fill), candle 2 follows.
    candles = [
        _c(NOW, 106.0, 110.0),                 # above the zone — no touch
        _c(NOW + timedelta(minutes=5), 94.0, 106.0),  # wicks into the zone — fill here
        _c(NOW + timedelta(minutes=10), 96.0, 99.0),  # post-fill
    ]
    post = _post_fill_candles(candles, 95.0, 105.0)
    assert post == candles[2:]


def test_post_fill_excludes_pre_fill_target_spike() -> None:
    """A pre-fill candle that would tag the target must NOT be walked (no fictitious win)."""
    # Long zone (95,105), TP would be ~120. Candle 0 spikes to 130 but its low (110) never
    # reaches the zone, so the limit hasn't filled yet — it must be excluded.
    candles = [
        _c(NOW, 110.0, 130.0),                        # huge up-spike BEFORE any fill
        _c(NOW + timedelta(minutes=5), 94.0, 100.0),  # price finally dips into the zone → fill
        _c(NOW + timedelta(minutes=10), 96.0, 98.0),
    ]
    post = _post_fill_candles(candles, 95.0, 105.0)
    assert post == candles[2:]
    assert candles[0] not in post  # the pre-fill 130 spike can never book a TP


def test_post_fill_empty_when_zone_never_touched() -> None:
    candles = [_c(NOW, 106.0, 110.0), _c(NOW + timedelta(minutes=5), 107.0, 112.0)]
    assert _post_fill_candles(candles, 95.0, 105.0) == []


def test_post_fill_empty_when_zone_unknown() -> None:
    candles = [_c(NOW, 94.0, 106.0)]
    assert _post_fill_candles(candles, None, None) == []


def test_post_fill_empty_when_fill_is_last_candle() -> None:
    candles = [_c(NOW, 106.0, 110.0), _c(NOW + timedelta(minutes=5), 94.0, 106.0)]
    assert _post_fill_candles(candles, 95.0, 105.0) == []


# --------------------------------------------------------------------------------------
# _reconcile_entry_bar — walks only the post-fill sub-candles of a just-opened trade
# --------------------------------------------------------------------------------------


def _trade(entry_time: datetime, *, zone=(95.0, 105.0)) -> SimpleNamespace:
    return SimpleNamespace(
        trade_id=uuid.uuid4(),
        symbol="BTCUSDT",
        direction="long",
        entry_time=entry_time,
        trade_metadata={"entry_zone_low": zone[0], "entry_zone_high": zone[1]},
    )


async def _run(monkeypatch, *, trades, sub_candles, observe_tf="5m", tf="15m"):
    """Drive _reconcile_entry_bar with patched collaborators; return (report, captured)."""
    monkeypatch.setattr(settings.observer, "observe_timeframe", observe_tf)

    async def candles_between(*_a: Any, **_k: Any) -> list[dict[str, Any]]:
        return sub_candles

    async def open_trades_for(*_a: Any, **_k: Any) -> list[Any]:
        return trades

    captured: dict[str, Any] = {}

    async def advance_trade(_s, _c, trade, candles, report, **kwargs):
        captured["candles"] = candles
        captured["intrabar"] = kwargs.get("intrabar")
        captured["trade_id"] = trade.trade_id
        report.closed += 1  # simulate a same-bar stop-out

    monkeypatch.setattr(orchestrator.state, "candles_between", candles_between)
    monkeypatch.setattr(orchestrator, "open_trades_for", open_trades_for)
    monkeypatch.setattr(orchestrator, "advance_trade", advance_trade)

    report = TickReport(now=NOW)
    await _reconcile_entry_bar(
        object(), object(), symbol="BTCUSDT", tf=tf, now=NOW,
        feature_row={"atr_14": 1.0}, report=report, run_id=None,
    )
    return report, captured


@pytest.mark.asyncio
async def test_walks_only_post_fill_candles_and_books_same_bar_exit(monkeypatch) -> None:
    sub = [
        _c(NOW, 106.0, 110.0),                        # pre-fill
        _c(NOW + timedelta(minutes=5), 94.0, 106.0),  # fill
        _c(NOW + timedelta(minutes=10), 88.0, 96.0),  # post-fill: stop-out territory
    ]
    report, captured = await _run(monkeypatch, trades=[_trade(NOW)], sub_candles=sub)
    assert captured["candles"] == sub[2:]   # only the post-fill candle is walked
    assert captured["intrabar"] is True
    assert report.closed == 1
    assert any("entry-bar exit" in n for n in report.notes)


@pytest.mark.asyncio
async def test_skips_trade_opened_on_a_prior_bar(monkeypatch) -> None:
    prior = _trade(NOW - timedelta(minutes=15))  # entry_time < now → already managed
    sub = [_c(NOW, 94.0, 106.0), _c(NOW + timedelta(minutes=5), 96.0, 99.0)]
    report, captured = await _run(monkeypatch, trades=[prior], sub_candles=sub)
    assert captured == {}        # advance_trade never called
    assert report.closed == 0


@pytest.mark.asyncio
async def test_no_finer_timeframe_is_a_no_op(monkeypatch) -> None:
    # observe_timeframe == tf → can't reconstruct intrabar order, so don't touch the trade.
    called = {"candles_between": False}

    async def candles_between(*_a: Any, **_k: Any) -> list[dict[str, Any]]:
        called["candles_between"] = True
        return []

    monkeypatch.setattr(settings.observer, "observe_timeframe", "15m")
    monkeypatch.setattr(orchestrator.state, "candles_between", candles_between)
    report = TickReport(now=NOW)
    await _reconcile_entry_bar(
        object(), object(), symbol="BTCUSDT", tf="15m", now=NOW,
        feature_row={}, report=report, run_id=None,
    )
    assert called["candles_between"] is False
    assert report.closed == 0
