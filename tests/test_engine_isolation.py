"""Isolation tests enabled by the Part 3 SOLID split.

Before the split these paths lived inside the 1,324-line ``detector.py`` and could only be
exercised through a full ``evaluate_now`` wiring. Now ``closing``, ``invalidation`` and the
segregated LLM role Protocols are testable directly with injected fakes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from ats.config import settings
from ats.engine import closing, invalidation
from ats.engine.reports import TickReport
from ats.execution.reconcile import ExitResult
from ats.llm.client import Adjudicator, LlmClient, MockClient, Observer, Reflector


class _FakeSession:
    def add(self, _obj: Any) -> None:
        return None

    async def flush(self) -> None:
        return None


def _trade(**over: Any) -> SimpleNamespace:
    base = dict(
        trade_id=uuid.uuid4(),
        plan_id=uuid.uuid4(),
        setup_id=uuid.uuid4(),
        symbol="BTCUSDT",
        direction="long",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=[110.0],
        size_pct=0.1,
        notional_usd=300.0,
        margin_usd=100.0,
        leverage=3.0,
        status="open",
        trade_metadata={},
    )
    base.update(over)
    return SimpleNamespace(**base)


# --- LLM role interface segregation --------------------------------------------------------


def test_mock_client_satisfies_each_segregated_role() -> None:
    c = MockClient()
    assert isinstance(c, Adjudicator)
    assert isinstance(c, Observer)
    assert isinstance(c, Reflector)
    assert isinstance(c, LlmClient)


def test_reflector_role_is_narrower_than_full_client() -> None:
    # A fake that only implements reflect_trade is a Reflector but not the full LlmClient,
    # so closing.close_trade (which only needs Reflector) accepts it.
    class OnlyReflector:
        async def reflect_trade(self, env: dict[str, Any], *, symbol: str) -> Any:
            return None, None

    only = OnlyReflector()
    assert isinstance(only, Reflector)
    assert not isinstance(only, LlmClient)


# --- closing.close_trade in isolation ------------------------------------------------------


async def test_close_trade_records_outcome_and_reflects(monkeypatch) -> None:
    trade = _trade()
    closed = SimpleNamespace(exit_price=110.0, exit_reason="target", pnl_pct=0.05)

    async def fake_close_paper_trade(session, trade_id, exit_result, *, equity_usd, size_pct):
        return closed

    monkeypatch.setattr(closing, "close_paper_trade", fake_close_paper_trade)
    monkeypatch.setattr(settings, "memory_enabled", True)

    seen: dict[str, Any] = {}

    async def fake_reflect(session, client, trade_, closed_result):
        seen["client"] = client

    import ats.learning.post_mortem as pm

    monkeypatch.setattr(pm, "reflect_and_store", fake_reflect)

    class FakeReflector:
        async def reflect_trade(self, env: dict[str, Any], *, symbol: str) -> Any:
            return None, None

    client = FakeReflector()
    report = TickReport()
    await closing.close_trade(
        _FakeSession(),
        client,
        trade,
        ExitResult(110.0, datetime(2026, 1, 1), "target", 0.05),
        report,
        atr_at_close=2.0,
    )

    assert report.closed == 1
    ct = report.closed_trades[0]
    assert ct.exit_price == 110.0
    assert ct.exit_reason == "target"
    assert ct.pnl_usd == 15.0  # notional 300 * 0.05
    assert ct.atr_at_close == 2.0
    assert seen["client"] is client  # the segregated Reflector was forwarded to the post-mortem


async def test_close_trade_skips_reflection_when_memory_disabled(monkeypatch) -> None:
    trade = _trade()
    closed = SimpleNamespace(exit_price=110.0, exit_reason="target", pnl_pct=0.05)

    async def fake_close_paper_trade(session, trade_id, exit_result, *, equity_usd, size_pct):
        return closed

    monkeypatch.setattr(closing, "close_paper_trade", fake_close_paper_trade)
    monkeypatch.setattr(settings, "memory_enabled", False)

    report = TickReport()
    await closing.close_trade(
        _FakeSession(),
        MockClient(),
        trade,
        ExitResult(110.0, datetime(2026, 1, 1), "target", 0.05),
        report,
    )
    assert report.closed == 1


# --- invalidation.handle_invalidation in isolation -----------------------------------------


async def test_handle_invalidation_hard_kills_plan_and_closes_trade(monkeypatch) -> None:
    setup = SimpleNamespace(
        setup_id=uuid.uuid4(), symbol="BTCUSDT", invalidation_rules=[], status="active"
    )
    trade = _trade(setup_id=setup.setup_id)
    plan = SimpleNamespace(status="active")

    monkeypatch.setattr(invalidation, "evaluate_invalidation", lambda *a, **k: "hard")

    async def fake_open_trades_for(session, symbol, *, run_id=None):
        return [trade]

    monkeypatch.setattr(invalidation, "open_trades_for", fake_open_trades_for)

    captured: list[str] = []

    async def fake_close_trade(session, client, trade_, exit_result, report, *, atr_at_close=None):
        report.closed += 1
        captured.append(exit_result.exit_reason)

    monkeypatch.setattr(invalidation, "close_trade", fake_close_trade)

    report = TickReport()
    feature_row = {"price": 94.0, "open_time": datetime(2026, 1, 1), "atr_14": 2.0}
    killed = await invalidation.handle_invalidation(
        _FakeSession(), None, plan, [setup], feature_row, None, True, report
    )

    assert killed is True
    assert plan.status == "invalidated"
    assert setup.status == "invalidated"
    assert report.plan_invalidated is True
    assert captured == ["invalidation"]
