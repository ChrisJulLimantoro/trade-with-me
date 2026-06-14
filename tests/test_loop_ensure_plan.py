"""Tests for loop._ensure_plan replan triggers (#6 regime-change replan)."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from ats.config import settings
from ats.engine import loop

NOW = datetime(2026, 1, 1, 12, 0)


def _plan(regime_cell: str, *, expires_in_h: int = 10) -> SimpleNamespace:
    return SimpleNamespace(
        regime_cell=regime_cell,
        expires_at=NOW + timedelta(hours=expires_in_h),
    )


async def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    plan: SimpleNamespace | None,
    regime_cell: str,
) -> dict[str, int]:
    calls = {"create": 0}

    async def open_positions(_s: Any, *, symbol=None, run_id=None) -> list:
        return []

    async def active_plan(_s: Any, _symbol: str, *, run_id=None) -> Any:
        return plan

    async def latest_regime(_s: Any, *, before_ts=None) -> dict:
        return {"regime_cell": regime_cell}

    async def create_plan(*_a: Any, **_k: Any) -> Any:
        calls["create"] += 1
        return SimpleNamespace(setups=[1, 2])

    monkeypatch.setattr(loop.state, "open_positions", open_positions)
    monkeypatch.setattr(loop.state, "active_plan", active_plan)
    monkeypatch.setattr(loop.state, "latest_regime", latest_regime)
    monkeypatch.setattr(loop, "create_plan", create_plan)
    return calls


async def test_no_replan_when_regime_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(settings, "replan_on_regime_change", True)
    monkeypatch.setattr(settings, "plan_refresh_bars", 16)
    calls = await _patch(monkeypatch, plan=_plan("bull-high"), regime_cell="bull-high")

    created, bars, setups = await loop._ensure_plan(
        None, None, symbol="BTCUSDT", tf="15m", now=NOW, bars_since_plan=1
    )

    assert created is False
    assert calls["create"] == 0
    assert bars == 2


async def test_replan_when_regime_cell_flips(monkeypatch) -> None:
    monkeypatch.setattr(settings, "replan_on_regime_change", True)
    monkeypatch.setattr(settings, "plan_refresh_bars", 16)
    calls = await _patch(monkeypatch, plan=_plan("bull-high"), regime_cell="bear-low")

    created, bars, setups = await loop._ensure_plan(
        None, None, symbol="BTCUSDT", tf="15m", now=NOW, bars_since_plan=1
    )

    assert created is True
    assert calls["create"] == 1
    assert setups == 2


async def test_no_replan_on_regime_flip_when_flag_off(monkeypatch) -> None:
    monkeypatch.setattr(settings, "replan_on_regime_change", False)
    monkeypatch.setattr(settings, "plan_refresh_bars", 16)
    calls = await _patch(monkeypatch, plan=_plan("bull-high"), regime_cell="bear-low")

    created, _bars, _setups = await loop._ensure_plan(
        None, None, symbol="BTCUSDT", tf="15m", now=NOW, bars_since_plan=1
    )

    assert created is False
    assert calls["create"] == 0
