"""Detector entry-loop tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from ats.config import settings
from ats.engine import detector
from ats.execution.reconcile import BarStep, TradeState
from ats.llm.schemas import ConfirmOutput, LlmResult, ObservationOutput
from ats.risk.manager import RiskDecision


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.trade = SimpleNamespace(trade_metadata={})

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def get(self, _model: type, _id: uuid.UUID) -> Any:
        return self.trade


class QueueClient:
    def __init__(self, actions: list[str]) -> None:
        self.actions = actions
        self.envelopes: list[dict[str, Any]] = []

    async def confirm_setup(
        self, envelope: dict[str, Any], *, symbol: str
    ) -> tuple[ConfirmOutput, LlmResult]:
        self.envelopes.append(envelope)
        action = self.actions.pop(0)
        return (
            ConfirmOutput(action=action, reason=action),
            LlmResult(parse_ok=True, model="test", mock=True, raw={"action": action}),
        )


class ObserveClient:
    def __init__(self) -> None:
        self.envelope: dict[str, Any] | None = None

    async def observe_trade(
        self, envelope: dict[str, Any], *, symbol: str
    ) -> tuple[ObservationOutput, LlmResult]:
        self.envelope = envelope
        return (
            ObservationOutput(action="HOLD", reason="hold"),
            LlmResult(parse_ok=True, model="test", mock=True, raw={"action": "HOLD"}),
        )


def _setup(
    *,
    plan_id: uuid.UUID,
    direction: str = "long",
    zone: tuple[float, float] = (95.0, 105.0),
    setup_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        setup_id=setup_id or uuid.uuid4(),
        plan_id=plan_id,
        symbol="BTCUSDT",
        direction=direction,
        status="active",
        entry_zone_low=zone[0],
        entry_zone_high=zone[1],
        stop_loss=90.0 if direction == "long" else 110.0,
        take_profit=[120.0] if direction == "long" else [80.0],
        size_pct=0.05,
        hard_rules=[],
        soft_rules=[],
        invalidation_rules=[],
        expires_at=datetime(2026, 1, 1) + timedelta(days=1),
        detected_at=None,
    )


async def _patch_detector(
    monkeypatch: pytest.MonkeyPatch,
    setups: list[Any],
    *,
    regime_cell: str = "side-low",
) -> list[float]:
    plan_id = setups[0].plan_id
    plan = SimpleNamespace(
        plan_id=plan_id,
        symbol="BTCUSDT",
        expires_at=datetime(2026, 1, 1) + timedelta(days=1),
        market_bias="bullish",
        regime_cell=regime_cell,
        status="active",
    )
    opened_entries: list[float] = []

    async def active_plan(_session: Any, _symbol: str, *, run_id: str | None = None) -> Any:
        return plan

    async def plan_setups(_session: Any, _plan_id: uuid.UUID) -> list[Any]:
        return setups

    async def open_positions(
        _session: Any, *, symbol: str | None = None, run_id: str | None = None
    ) -> list[dict[str, Any]]:
        return []

    async def reconcile(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def invalidation(*_args: Any, **_kwargs: Any) -> bool:
        return False

    def approve(*_args: Any, **_kwargs: Any) -> RiskDecision:
        return RiskDecision(
            True,
            0.1,
            ["approved"],
            leverage=1.0,
            margin_usd=100.0,
            risk_usd=10.0,
            notional_usd=100.0,
        )

    async def open_trade(_session: Any, _setup: dict[str, Any], **kwargs: Any) -> uuid.UUID:
        opened_entries.append(float(kwargs["entry_price"]))
        return uuid.uuid4()

    monkeypatch.setattr(detector.state, "active_plan", active_plan)
    monkeypatch.setattr(detector.state, "plan_setups", plan_setups)
    monkeypatch.setattr(detector.state, "open_positions", open_positions)
    monkeypatch.setattr(detector, "_reconcile_open_trades", reconcile)
    monkeypatch.setattr(detector, "_handle_invalidation", invalidation)
    monkeypatch.setattr(detector, "assess", approve)
    monkeypatch.setattr(detector, "open_paper_trade", open_trade)
    return opened_entries


async def test_wick_triggered_setup_opens_at_simulated_entry_price(monkeypatch) -> None:
    monkeypatch.setattr(settings, "entry_trigger_mode", "wick_limit")
    plan_id = uuid.uuid4()
    setups = [_setup(plan_id=plan_id, direction="long", zone=(101.0, 105.0))]
    opened_entries = await _patch_detector(monkeypatch, setups)

    report = await detector.evaluate_now(
        FakeSession(),
        QueueClient(["CONFIRM"]),
        symbol="BTCUSDT",
        tf="15m",
        feature_row={
            "open_time": datetime(2026, 1, 1),
            "price": 100.0,
            "close": 100.0,
            "low": 99.0,
            "high": 102.0,
        },
        prev_row=None,
    )

    assert report.opened == 1
    assert opened_entries == [105.0]


async def test_stops_evaluating_setups_after_one_opens(monkeypatch) -> None:
    monkeypatch.setattr(settings, "entry_trigger_mode", "close")
    plan_id = uuid.uuid4()
    setups = [_setup(plan_id=plan_id), _setup(plan_id=plan_id)]
    opened_entries = await _patch_detector(monkeypatch, setups)
    client = QueueClient(["CONFIRM", "CONFIRM"])

    report = await detector.evaluate_now(
        FakeSession(),
        client,
        symbol="BTCUSDT",
        tf="15m",
        feature_row={
            "open_time": datetime(2026, 1, 1),
            "price": 100.0,
            "close": 100.0,
            "low": 99.0,
            "high": 101.0,
        },
        prev_row=None,
    )

    assert report.opened == 1
    assert report.confirm_calls == 1
    assert len(opened_entries) == 1


async def test_waited_setup_does_not_block_later_setup(monkeypatch) -> None:
    monkeypatch.setattr(settings, "entry_trigger_mode", "close")
    plan_id = uuid.uuid4()
    setups = [_setup(plan_id=plan_id), _setup(plan_id=plan_id)]
    opened_entries = await _patch_detector(monkeypatch, setups)
    client = QueueClient(["WAIT", "CONFIRM"])

    report = await detector.evaluate_now(
        FakeSession(),
        client,
        symbol="BTCUSDT",
        tf="15m",
        feature_row={
            "open_time": datetime(2026, 1, 1),
            "price": 100.0,
            "close": 100.0,
            "low": 99.0,
            "high": 101.0,
        },
        prev_row=None,
    )

    assert report.opened == 1
    assert report.confirm_calls == 2
    assert opened_entries == [100.0]


async def test_sideways_trade_persists_range_exit_metadata(monkeypatch) -> None:
    monkeypatch.setattr(settings, "entry_trigger_mode", "close")
    monkeypatch.setattr(settings, "sideways_exit_mode", "range")
    monkeypatch.setattr(settings, "sideways_scale_out_frac", 0.75)
    monkeypatch.setattr(settings, "sideways_trail_atr_mult", 2.0)
    monkeypatch.setattr(settings, "sideways_early_stop_mode", "trail")
    monkeypatch.setattr(settings, "sideways_early_trail_arm_atr", 1.0)
    monkeypatch.setattr(settings, "sideways_early_trail_atr_mult", 2.0)
    plan_id = uuid.uuid4()
    setups = [_setup(plan_id=plan_id)]
    await _patch_detector(monkeypatch, setups, regime_cell="side-high")
    session = FakeSession()

    report = await detector.evaluate_now(
        session,
        QueueClient(["CONFIRM"]),
        symbol="BTCUSDT",
        tf="15m",
        feature_row={
            "open_time": datetime(2026, 1, 1),
            "price": 100.0,
            "close": 100.0,
            "low": 99.0,
            "high": 101.0,
        },
        prev_row=None,
    )

    md = session.trade.trade_metadata
    assert report.opened == 1
    assert md["regime_cell"] == "side-high"
    assert md["exit_mode"] == "range"
    assert md["exit_scale_out_frac"] == pytest.approx(0.75)
    assert md["exit_trail_atr_mult"] == pytest.approx(2.0)
    assert md["exit_breakeven_requires_tp1"] is True
    assert md["exit_trail_after_tp1_only"] is True
    assert md["exit_early_stop_mode"] == "trail"
    assert md["exit_early_trail_arm_atr"] == pytest.approx(1.0)
    assert md["exit_early_trail_atr_mult"] == pytest.approx(2.0)
    assert md["entry_zone_low"] == pytest.approx(95.0)
    assert md["entry_zone_high"] == pytest.approx(105.0)


async def test_trending_trade_persists_trend_exit_metadata(monkeypatch) -> None:
    monkeypatch.setattr(settings, "entry_trigger_mode", "close")
    monkeypatch.setattr(settings, "scale_out_frac", 0.5)
    monkeypatch.setattr(settings, "trail_atr_mult", 1.5)
    monkeypatch.setattr(settings, "early_stop_mode", "trail")
    monkeypatch.setattr(settings, "early_trail_arm_atr", 1.0)
    monkeypatch.setattr(settings, "early_trail_atr_mult", 2.0)
    plan_id = uuid.uuid4()
    setups = [_setup(plan_id=plan_id)]
    await _patch_detector(monkeypatch, setups, regime_cell="bull-low")
    session = FakeSession()

    report = await detector.evaluate_now(
        session,
        QueueClient(["CONFIRM"]),
        symbol="BTCUSDT",
        tf="15m",
        feature_row={
            "open_time": datetime(2026, 1, 1),
            "price": 100.0,
            "close": 100.0,
            "low": 99.0,
            "high": 101.0,
        },
        prev_row=None,
    )

    md = session.trade.trade_metadata
    assert report.opened == 1
    assert md["regime_cell"] == "bull-low"
    assert md["exit_mode"] == "trend"
    assert md["exit_scale_out_frac"] == pytest.approx(0.5)
    assert md["exit_trail_atr_mult"] == pytest.approx(1.5)
    assert md["exit_breakeven_requires_tp1"] is False
    assert md["exit_trail_after_tp1_only"] is False
    assert md["exit_early_stop_mode"] == "trail"
    assert md["exit_early_trail_arm_atr"] == pytest.approx(1.0)
    assert md["exit_early_trail_atr_mult"] == pytest.approx(2.0)
    assert md["entry_zone_low"] == pytest.approx(95.0)
    assert md["entry_zone_high"] == pytest.approx(105.0)


async def test_sideways_exit_policy_passes_range_knobs_to_step_trade(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sideways_exit_mode", "range")
    monkeypatch.setattr(settings, "sideways_scale_out_frac", 0.75)
    monkeypatch.setattr(settings, "sideways_trail_atr_mult", 2.0)
    monkeypatch.setattr(settings, "sideways_breakeven_requires_tp1", True)
    monkeypatch.setattr(settings, "sideways_trail_after_tp1_only", True)
    monkeypatch.setattr(settings, "sideways_early_stop_mode", "trail")
    monkeypatch.setattr(settings, "sideways_early_trail_arm_atr", 1.0)
    monkeypatch.setattr(settings, "sideways_early_trail_atr_mult", 2.0)
    policy = detector._exit_policy_for_regime("side-low")
    trade = SimpleNamespace(
        trade_id=uuid.uuid4(),
        trade_metadata=detector._exit_policy_metadata(policy),
        entry_time=datetime(2026, 1, 1) - timedelta(minutes=15),
        direction="long",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=[110.0, 120.0],
        liq_price=None,
        size_pct=0.1,
    )
    captured: dict[str, Any] = {}

    def fake_step_trade(*_args: Any, **kwargs: Any) -> BarStep:
        captured.update(kwargs)
        return BarStep(state=TradeState())

    monkeypatch.setattr(detector, "step_trade", fake_step_trade)

    await detector._advance_trade(
        FakeSession(),
        None,
        trade,
        [{"open_time": datetime(2026, 1, 1), "high": 105.0, "low": 99.0, "close": 104.0}],
        detector.TickReport(),
        atr=5.0,
        feature_row=None,
    )

    assert captured["scale_out_frac"] == pytest.approx(0.75)
    assert captured["trail_atr_mult"] == pytest.approx(2.0)
    assert captured["breakeven_requires_tp1"] is True
    assert captured["trail_after_tp1_only"] is True
    assert captured["early_stop_mode"] == "trail"
    assert captured["early_trail_arm_atr"] == pytest.approx(1.0)
    assert captured["early_trail_atr_mult"] == pytest.approx(2.0)


async def test_trend_exit_policy_passes_global_knobs_to_step_trade(monkeypatch) -> None:
    monkeypatch.setattr(settings, "scale_out_frac", 0.5)
    monkeypatch.setattr(settings, "trail_atr_mult", 1.5)
    monkeypatch.setattr(settings, "early_stop_mode", "trail")
    monkeypatch.setattr(settings, "early_trail_arm_atr", 1.0)
    monkeypatch.setattr(settings, "early_trail_atr_mult", 2.0)
    policy = detector._exit_policy_for_regime("bull-low")
    trade = SimpleNamespace(
        trade_id=uuid.uuid4(),
        trade_metadata=detector._exit_policy_metadata(policy),
        entry_time=datetime(2026, 1, 1) - timedelta(minutes=15),
        direction="long",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=[110.0, 120.0],
        liq_price=None,
        size_pct=0.1,
    )
    captured: dict[str, Any] = {}

    def fake_step_trade(*_args: Any, **kwargs: Any) -> BarStep:
        captured.update(kwargs)
        return BarStep(state=TradeState())

    monkeypatch.setattr(detector, "step_trade", fake_step_trade)

    await detector._advance_trade(
        FakeSession(),
        None,
        trade,
        [{"open_time": datetime(2026, 1, 1), "high": 105.0, "low": 99.0, "close": 104.0}],
        detector.TickReport(),
        atr=5.0,
        feature_row=None,
    )

    assert captured["scale_out_frac"] == pytest.approx(0.5)
    assert captured["trail_atr_mult"] == pytest.approx(1.5)
    assert captured["breakeven_requires_tp1"] is False
    assert captured["trail_after_tp1_only"] is False
    assert captured["early_stop_mode"] == "trail"
    assert captured["early_trail_arm_atr"] == pytest.approx(1.0)
    assert captured["early_trail_atr_mult"] == pytest.approx(2.0)


async def test_observer_envelope_includes_hold_and_stagnation_context(monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_hold_bars", 48)
    monkeypatch.setattr(settings, "observe_stale_hold_progress", 0.75)
    monkeypatch.setattr(settings, "observe_stale_unrealized_abs_pct", 0.001)
    monkeypatch.setattr(settings, "observe_stale_mfe_pct", 0.003)
    entry_time = datetime(2026, 1, 1, 0, 0)
    now = entry_time + timedelta(minutes=90)
    policy = detector._exit_policy_for_regime("bull-low")
    client = ObserveClient()
    trade = SimpleNamespace(
        trade_id=uuid.uuid4(),
        plan_id=uuid.uuid4(),
        setup_id=uuid.uuid4(),
        symbol="BTCUSDT",
        direction="long",
        entry_time=entry_time,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=[110.0, 120.0],
        size_pct=0.1,
        leverage=3.0,
        margin_usd=100.0,
        notional_usd=300.0,
        trade_metadata={
            **detector._exit_policy_metadata(policy),
            "entry_zone_low": 95.0,
            "entry_zone_high": 105.0,
            "expires_at": (entry_time + timedelta(hours=2)).isoformat(),
            "hold_window_seconds": 7200.0,
        },
    )
    state = TradeState(max_favorable_pnl_pct=0.001, max_adverse_pnl_pct=-0.002)

    closed = await detector._observe_and_adjust(
        FakeSession(),
        client,
        trade,
        {"direction": "long", "entry_price": 100.0, "stop_loss": 95.0, "take_profit": [110.0]},
        {"open_time": now, "high": 100.2, "low": 99.8, "close": 100.05},
        state,
        detector.TickReport(),
        feature_row={
            "atr_14": 2.0,
            "close": 100.05,
            "macd_hist": 0.0,
            "cvd_slope_10": 0.0,
            "vol_zscore_20": 0.5,
        },
        policy=policy,
    )

    assert closed is False
    assert client.envelope is not None
    hold = client.envelope["hold"]
    progress = client.envelope["progress"]
    assert hold["held_minutes"] == pytest.approx(90.0)
    assert hold["held_bars_estimate"] == pytest.approx(36.0)
    assert hold["current_window_progress"] == pytest.approx(0.75)
    assert client.envelope["unrealized_pct"] == pytest.approx(0.0015)
    assert client.envelope["unrealized_margin_pct"] == pytest.approx(0.0015)
    assert client.envelope["position_value"] == {
        "margin_usd": 100.0,
        "notional_usd": 300.0,
        "leverage": 3.0,
    }
    assert progress["pnl_basis"] == "margin"
    assert progress["max_favorable_pnl_pct"] == pytest.approx(0.003)
    assert progress["max_adverse_pnl_pct"] == pytest.approx(-0.006)
    assert progress["stale_candidate"] is False
    observer_context = client.envelope["observer_context"]
    assert observer_context["entry_quality"]["entry_vs_zone"] == "mid_zone"
    assert observer_context["entry_quality"]["entry_to_zone_low_atr"] == pytest.approx(2.5)
    assert observer_context["entry_quality"]["entry_to_zone_high_atr"] == pytest.approx(2.5)
    assert observer_context["excursion"]["current_r"] == pytest.approx(0.01)
    assert observer_context["excursion"]["mfe_r"] == pytest.approx(0.02)
    assert observer_context["excursion"]["mae_r"] == pytest.approx(-0.04)
    assert observer_context["thesis_health"]["status"] == "healthy"
    assert observer_context["recommended_pressure"] == "hold"


async def test_observer_context_marks_failed_short_as_broken(monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_hold_bars", 48)
    entry_time = datetime(2026, 1, 1, 0, 0)
    now = entry_time + timedelta(minutes=30)
    policy = detector._exit_policy_for_regime("bear-low")
    client = ObserveClient()
    trade = SimpleNamespace(
        trade_id=uuid.uuid4(),
        plan_id=uuid.uuid4(),
        setup_id=uuid.uuid4(),
        symbol="BTCUSDT",
        direction="short",
        entry_time=entry_time,
        entry_price=100.0,
        stop_loss=105.0,
        take_profit=[95.0, 90.0],
        size_pct=0.1,
        leverage=3.0,
        margin_usd=100.0,
        notional_usd=300.0,
        trade_metadata={
            **detector._exit_policy_metadata(policy),
            "entry_zone_low": 99.0,
            "entry_zone_high": 101.0,
            "expires_at": (entry_time + timedelta(hours=2)).isoformat(),
            "hold_window_seconds": 7200.0,
        },
    )
    state = TradeState(max_favorable_pnl_pct=0.005, max_adverse_pnl_pct=-0.04)

    closed = await detector._observe_and_adjust(
        FakeSession(),
        client,
        trade,
        {"direction": "short", "entry_price": 100.0, "stop_loss": 105.0, "take_profit": [95.0]},
        {"open_time": now, "high": 103.0, "low": 99.5, "close": 102.5},
        state,
        detector.TickReport(),
        feature_row={
            "atr_14": 2.0,
            "close": 102.5,
            "macd_hist": 1.0,
            "cvd_slope_10": 1.0,
            "vol_zscore_20": -0.5,
            "rsi_14": 30.0,
            "ema_20": 103.0,
            "ema_50": 104.0,
        },
        policy=policy,
    )

    assert closed is False
    assert client.envelope is not None
    observer_context = client.envelope["observer_context"]
    assert observer_context["excursion"]["current_r"] == pytest.approx(-0.5)
    assert observer_context["excursion"]["mfe_r"] == pytest.approx(0.1)
    assert observer_context["excursion"]["mae_r"] == pytest.approx(-0.8)
    assert observer_context["thesis_health"]["status"] == "broken"
    assert observer_context["thesis_health"]["directional_momentum"] == "against"
    assert observer_context["thesis_health"]["cvd_agreement"] == "against"
    assert observer_context["thesis_health"]["squeeze_risk"] == "against_trade"
    assert observer_context["recommended_pressure"] == "exit"


# --- #1 entry-confirmation gate --------------------------------------------------------


async def test_entry_confirmation_blocks_unconfirmed_setup(monkeypatch) -> None:
    monkeypatch.setattr(settings, "entry_trigger_mode", "close")
    monkeypatch.setattr(settings, "entry_confirmation_enabled", True)
    plan_id = uuid.uuid4()
    setups = [_setup(plan_id=plan_id, direction="long")]
    opened_entries = await _patch_detector(monkeypatch, setups)
    client = QueueClient(["CONFIRM"])

    # Close moved DOWN vs prev (against the long) → not confirmed; no confirm call, no open.
    report = await detector.evaluate_now(
        FakeSession(),
        client,
        symbol="BTCUSDT",
        tf="15m",
        feature_row={
            "open_time": datetime(2026, 1, 1),
            "price": 100.0, "close": 100.0, "low": 99.0, "high": 101.0,
            "cvd_slope_10": 1.0, "macd_hist": 0.5,
        },
        prev_row={"price": 101.0, "close": 101.0},
    )

    assert report.opened == 0
    assert report.confirm_calls == 0
    assert opened_entries == []
    assert any("awaiting confirmation" in n for n in report.notes)


async def test_entry_confirmation_allows_confirmed_setup(monkeypatch) -> None:
    monkeypatch.setattr(settings, "entry_trigger_mode", "close")
    monkeypatch.setattr(settings, "entry_confirmation_enabled", True)
    plan_id = uuid.uuid4()
    setups = [_setup(plan_id=plan_id, direction="long")]
    opened_entries = await _patch_detector(monkeypatch, setups)

    # Close moved UP vs prev (with the long) and macd_hist agrees → confirmed.
    report = await detector.evaluate_now(
        FakeSession(),
        QueueClient(["CONFIRM"]),
        symbol="BTCUSDT",
        tf="15m",
        feature_row={
            "open_time": datetime(2026, 1, 1),
            "price": 100.0, "close": 100.0, "low": 99.0, "high": 101.0,
            "cvd_slope_10": 1.0, "macd_hist": 0.5,
        },
        prev_row={"price": 99.0, "close": 99.0},
    )

    assert report.opened == 1
    assert opened_entries == [100.0]


# --- #5 observer-call gate -------------------------------------------------------------


def test_observer_call_due_consults_on_decaying_or_broken() -> None:
    assert detector._observer_call_due("broken", stale_candidate=False, bars=1) is True
    assert detector._observer_call_due("decaying", stale_candidate=False, bars=1) is True


def test_observer_call_due_consults_when_stale() -> None:
    assert detector._observer_call_due("healthy", stale_candidate=True, bars=1) is True


def test_observer_call_due_skips_healthy_off_cadence(monkeypatch) -> None:
    monkeypatch.setattr(settings, "observe_health_fallback_bars", 12)
    assert detector._observer_call_due("healthy", stale_candidate=False, bars=7) is False


def test_observer_call_due_fires_on_fallback_cadence(monkeypatch) -> None:
    monkeypatch.setattr(settings, "observe_health_fallback_bars", 12)
    assert detector._observer_call_due("healthy", stale_candidate=False, bars=12) is True
