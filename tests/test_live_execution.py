"""Tests for the Binance testnet live-execution layer (pure logic + CLI guards).

No network: we exercise quantity rounding, side mapping, and the CLI safety guard. The
actual order placement is a thin python-binance pass-through covered by the dry/round-trip
manual verification in the plan.
"""

from __future__ import annotations

import pytest
import typer

from ats.config import settings
from ats.execution.binance_futures import BinanceFuturesTestnet, SymbolFilters
from ats.execution.live import _side_for_open, _side_for_reduce


@pytest.fixture(autouse=True)
def _restore_settings():
    """Guard tests mutate the global settings singleton; snapshot + restore so test
    ordering is never affected (and the scalper profile applied here doesn't leak)."""
    saved = {
        "binance_testnet": settings.binance_testnet,
        "binance_api_key": settings.binance_api_key,
        "binance_api_secret": settings.binance_api_secret,
        "llm_mock": settings.llm_mock,
        "adjudication_enabled": settings.adjudication_enabled,
        "observe_enabled": settings.observer.observe_enabled,
        "memory_enabled": settings.memory_enabled,
        "strategy_profile": settings.strategy_profile,
    }
    try:
        yield
    finally:
        settings.binance_testnet = saved["binance_testnet"]
        settings.binance_api_key = saved["binance_api_key"]
        settings.binance_api_secret = saved["binance_api_secret"]
        settings.llm_mock = saved["llm_mock"]
        settings.adjudication_enabled = saved["adjudication_enabled"]
        settings.observer.observe_enabled = saved["observe_enabled"]
        settings.memory_enabled = saved["memory_enabled"]
        settings.strategy_profile = saved["strategy_profile"]


def _client_with_filters(**kw) -> BinanceFuturesTestnet:
    c = BinanceFuturesTestnet.__new__(BinanceFuturesTestnet)
    c._filters = {"SOLUSDT": SymbolFilters(**kw)}
    return c


def test_round_qty_floors_to_step():
    c = _client_with_filters(
        step_size=0.1, min_qty=0.1, tick_size=0.01, min_notional=5.0, qty_precision=1
    )
    assert c.round_qty("SOLUSDT", 1.27) == 1.2
    assert c.round_qty("SOLUSDT", 2.0) == 2.0


def test_round_qty_below_min_returns_zero():
    c = _client_with_filters(
        step_size=0.1, min_qty=1.0, tick_size=0.01, min_notional=5.0, qty_precision=1
    )
    assert c.round_qty("SOLUSDT", 0.5) == 0.0


def test_round_qty_requires_loaded_filters():
    c = BinanceFuturesTestnet.__new__(BinanceFuturesTestnet)
    c._filters = {}
    with pytest.raises(Exception):
        c.round_qty("SOLUSDT", 1.0)


def test_side_mapping():
    assert _side_for_open("long") == "BUY"
    assert _side_for_open("short") == "SELL"
    assert _side_for_reduce("long") == "SELL"   # closing/reducing a long sells
    assert _side_for_reduce("short") == "BUY"


@pytest.mark.asyncio
async def test_create_refuses_mainnet():
    from ats.execution.binance_futures import OrderError

    with pytest.raises(OrderError):
        await BinanceFuturesTestnet.create("k", "s", testnet=False)


def _reset_settings():
    settings.binance_testnet = True
    settings.binance_api_key = "k"
    settings.binance_api_secret = "s"
    settings.llm_mock = True
    settings.adjudication_enabled = False
    settings.observer.observe_enabled = False
    settings.memory_enabled = False


def test_guard_passes_with_safe_config():
    from ats.cli_commands.engine import _guard_live

    _reset_settings()
    _guard_live("scalper")  # must not raise


def test_guard_refuses_mainnet():
    from ats.cli_commands.engine import _guard_live

    _reset_settings()
    settings.binance_testnet = False
    with pytest.raises(typer.BadParameter):
        _guard_live("scalper")


def test_guard_refuses_missing_keys():
    from ats.cli_commands.engine import _guard_live

    _reset_settings()
    settings.binance_api_key = None
    with pytest.raises(typer.BadParameter):
        _guard_live("scalper")


def test_guard_refuses_llm_enabled():
    from ats.cli_commands.engine import _guard_live

    _reset_settings()
    settings.llm_mock = False
    with pytest.raises(typer.BadParameter):
        _guard_live("scalper")


class _FakeRaw:
    """Captures kwargs passed to futures_create_algo_order."""

    def __init__(self):
        self.calls = []

    async def futures_create_algo_order(self, **params):
        self.calls.append(params)
        return {"algoId": 999, "status": "NEW"}


def _algo_client() -> BinanceFuturesTestnet:
    """A client wired for conditional-order tests: fake raw client + loaded 0.01-tick filters."""
    c = BinanceFuturesTestnet.__new__(BinanceFuturesTestnet)
    c._client = _FakeRaw()
    c._recorder = None
    c._filters = {
        "SOLUSDT": SymbolFilters(
            step_size=0.001, min_qty=0.001, tick_size=0.01, min_notional=5.0, qty_precision=3
        )
    }
    return c


@pytest.mark.asyncio
async def test_place_conditional_stop_market_closeposition():
    c = _algo_client()
    algo_id = await c.place_conditional(
        "SOLUSDT", "SELL", "STOP_MARKET", trigger_price=120.0, close_position=True
    )
    assert algo_id == "999"
    sent = c._client.calls[0]
    # Conditional algo schema: algoType CONDITIONAL, subtype in 'type', trigger in 'triggerPrice'.
    assert sent["algoType"] == "CONDITIONAL"
    assert sent["type"] == "STOP_MARKET"
    assert sent["triggerPrice"] == 120.0
    assert sent["closePosition"] == "true"
    # closePosition is mutually exclusive with quantity/reduceOnly — neither must be sent.
    assert "quantity" not in sent and "reduceOnly" not in sent


@pytest.mark.asyncio
async def test_place_conditional_trailing_uses_callback_and_quantity():
    c = _algo_client()
    await c.place_conditional(
        "SOLUSDT", "SELL", "TRAILING_STOP_MARKET",
        callback_rate=1.5, activate_price=130.0, quantity=2.0, reduce_only=True,
    )
    sent = c._client.calls[0]
    assert sent["type"] == "TRAILING_STOP_MARKET"
    assert sent["callbackRate"] == 1.5
    assert sent["activatePrice"] == 130.0
    assert sent["quantity"] == 2.0
    assert sent["reduceOnly"] == "true"


def test_round_price_snaps_to_tick():
    # 0.01 tick: an 8-decimal trigger (as sent to Binance in the live-bleed bug) rounds to 2 dp.
    c = _client_with_filters(
        step_size=0.001, min_qty=0.001, tick_size=0.01, min_notional=5.0, qty_precision=3
    )
    assert c.round_price("SOLUSDT", 80.79546042) == 80.80
    assert c.round_price("SOLUSDT", 81.12755593) == 81.13
    # Coarser 0.1 tick: precision derives from the tick, rounds to nearest.
    c2 = _client_with_filters(
        step_size=0.1, min_qty=0.1, tick_size=0.1, min_notional=5.0, qty_precision=1
    )
    assert c2.round_price("SOLUSDT", 80.744) == 80.7
    assert c2.round_price("SOLUSDT", 80.751) == 80.8


def test_round_price_requires_loaded_filters():
    c = BinanceFuturesTestnet.__new__(BinanceFuturesTestnet)
    c._filters = {}
    with pytest.raises(Exception):
        c.round_price("SOLUSDT", 80.0)


@pytest.mark.asyncio
async def test_place_conditional_rounds_trigger_price():
    # The core fix: an over-precise trigger must reach the exchange snapped to the tick, not raw.
    c = _algo_client()
    await c.place_conditional(
        "SOLUSDT", "SELL", "STOP_MARKET", trigger_price=80.79546042, close_position=True
    )
    assert c._client.calls[0]["triggerPrice"] == 80.80


class _RetryProtClient:
    """Fake exchange client: first place_conditional raises, the rest succeed."""

    def __init__(self) -> None:
        self.place_calls = 0
        self.cancelled: list[str] = []
        self.closed = False

    async def place_conditional(self, symbol, side, order_type, **kw):
        self.place_calls += 1
        if self.place_calls == 1:
            from ats.execution.binance_futures import OrderError

            raise OrderError("transient -1111")
        return f"algo{self.place_calls}"

    async def cancel_conditional(self, symbol, algo_id):
        self.cancelled.append(algo_id)

    async def position_risk(self, symbol):  # only reached if we (wrongly) flatten
        self.closed = True
        return None


@pytest.mark.asyncio
async def test_place_protection_retries_before_flattening():
    from ats.execution.live import LiveContext

    ctx = LiveContext.__new__(LiveContext)
    ctx._client = _RetryProtClient()
    ctx._protection = {}
    await ctx.place_protection(
        "t1", "SOLUSDT", "long", stop_loss=80.0, take_profit=82.0
    )
    # First attempt failed (SL), retry placed SL+TP successfully → 3 calls total, no flatten.
    assert ctx._client.place_calls == 3
    assert ctx._client.closed is False
    assert "t1" in ctx._protection
