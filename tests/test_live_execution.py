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
async def test_place_conditional_rounds_quantity_to_step():
    # The quantity-side twin of the trigger-rounding fix: an unsteppable size is a -1111 reject.
    # 0.001 step → 1.23456789 must reach the exchange floored to 1.234.
    c = _algo_client()
    await c.place_conditional(
        "SOLUSDT", "SELL", "STOP_MARKET", trigger_price=120.0,
        reduce_only=True, quantity=1.23456789,
    )
    sent = c._client.calls[0]
    assert sent["quantity"] == 1.234
    assert sent["reduceOnly"] == "true"
    assert "closePosition" not in sent


class _RequeryRaw:
    """MARKET create returns FILLED with no fill price (the observed testnet behaviour)."""

    def __init__(self, requery: dict) -> None:
        self._requery = requery
        self.get_calls: list[dict] = []

    async def futures_create_order(self, **params):
        return {"orderId": 4185769521, "status": "FILLED", "executedQty": "160.53"}

    async def futures_get_order(self, **params):
        self.get_calls.append(params)
        return self._requery


def _market_client(raw) -> BinanceFuturesTestnet:
    c = BinanceFuturesTestnet.__new__(BinanceFuturesTestnet)
    c._client = raw
    c._recorder = None
    c._filters = {
        "SOLUSDT": SymbolFilters(
            step_size=0.001, min_qty=0.001, tick_size=0.01, min_notional=5.0, qty_precision=3
        )
    }
    return c


@pytest.mark.asyncio
async def test_market_order_requeries_when_response_has_no_fill_price():
    # Live symptom: `testnet_order ... status FILLED, executed_qty 160.53, fill 0.0` → the caller
    # stored exit_fill_price=None on all 19 trades. One re-query resolves the real average.
    raw = _RequeryRaw({"status": "FILLED", "executedQty": "160.53", "avgPrice": "103.42"})
    c = _market_client(raw)
    result = await c.market_order("SOLUSDT", "SELL", 160.53, reduce_only=True)
    assert result.avg_price == 103.42
    assert raw.get_calls == [{"symbol": "SOLUSDT", "orderId": 4185769521}]


@pytest.mark.asyncio
async def test_market_order_requery_falls_back_to_cum_quote():
    raw = _RequeryRaw({"status": "FILLED", "executedQty": "2.0", "cumQuote": "200.0"})
    c = _market_client(raw)
    result = await c.market_order("SOLUSDT", "SELL", 2.0, reduce_only=True)
    assert result.avg_price == 100.0


@pytest.mark.asyncio
async def test_market_order_skips_requery_when_fill_already_known():
    class _Raw(_RequeryRaw):
        async def futures_create_order(self, **params):
            return {"orderId": 1, "status": "FILLED", "executedQty": "1.0", "avgPrice": "99.5"}

    raw = _Raw({})
    c = _market_client(raw)
    result = await c.market_order("SOLUSDT", "SELL", 1.0)
    assert result.avg_price == 99.5
    assert raw.get_calls == []  # no wasted round-trip


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

    async def load_filters(self, symbol):
        return None

    async def position_risk(self, symbol):
        # Now read legitimately to size the reduce-only protective orders, so it can no longer
        # double as the "did we flatten?" sentinel — market_order below is that signal.
        return {"positionAmt": "1.0"}

    def round_qty(self, symbol, qty):
        return qty

    async def market_order(self, symbol, side, qty, **kw):  # only reached if we (wrongly) flatten
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


class _GetOrderClient:
    """Fake exchange client: returns a filled algo order with a fixed avgPrice.

    Protective ids are algoIds, so the lookup must hit the ALGO endpoint. ``get_order`` raises
    here to pin that down — routing an algoId to /fapi/v1/order answers -2013 live, which
    silently defeated every native exit-fill lookup.
    """

    def __init__(self, avg_price: float | None) -> None:
        self._avg_price = avg_price
        self.get_order_calls: list[tuple[str, str]] = []

    async def get_order(self, symbol, order_id):
        raise AssertionError("native fills must be read via get_algo_order, not get_order")

    async def get_algo_order(self, symbol, algo_id):
        self.get_order_calls.append((symbol, algo_id))
        return {"status": "FILLED", "avgPrice": self._avg_price}


@pytest.mark.asyncio
async def test_native_exit_fill_price_reads_sl_slot_for_sl_reason():
    from ats.execution.live import LiveContext

    ctx = LiveContext.__new__(LiveContext)
    ctx._client = _GetOrderClient(78.16)
    ctx._protection = {"t1": {"direction": "long", "sl": "algo-sl", "tp": "algo-tp"}}
    price = await ctx.native_exit_fill_price("t1", "SOLUSDT", "sl")
    assert price == 78.16
    assert ctx._client.get_order_calls == [("SOLUSDT", "algo-sl")]


@pytest.mark.asyncio
async def test_native_exit_fill_price_reads_tp_slot_for_tp_reason():
    from ats.execution.live import LiveContext

    ctx = LiveContext.__new__(LiveContext)
    ctx._client = _GetOrderClient(82.5)
    ctx._protection = {"t1": {"direction": "long", "sl": "algo-sl", "tp": "algo-tp"}}
    price = await ctx.native_exit_fill_price("t1", "SOLUSDT", "tp")
    assert price == 82.5
    assert ctx._client.get_order_calls == [("SOLUSDT", "algo-tp")]


@pytest.mark.asyncio
async def test_native_exit_fill_price_none_when_trade_untracked():
    from ats.execution.live import LiveContext

    ctx = LiveContext.__new__(LiveContext)
    ctx._client = _GetOrderClient(78.16)
    ctx._protection = {}
    assert await ctx.native_exit_fill_price("t1", "SOLUSDT", "sl") is None
    assert ctx._client.get_order_calls == []


# ---------------------------------------------------------------------------------------
# realized_costs settling — Binance income history is eventually consistent
# ---------------------------------------------------------------------------------------


async def _no_sleep(_seconds):
    """Collapse the retry backoff so the tests don't actually wait 5 seconds."""
    return None


class _IncomeClient:
    """realized_costs returns zeros until the Nth call, mimicking income-history lag."""

    def __init__(self, settle_on_call: int, settled: dict) -> None:
        self._settle_on = settle_on_call
        self._settled = settled
        self.calls = 0

    async def realized_costs(self, symbol, start_ms):
        self.calls += 1
        if self.calls < self._settle_on:
            return {"realized_pnl": 0.0, "commission": -3.33, "funding": 0.0}
        return self._settled


@pytest.mark.asyncio
async def test_realized_costs_retries_until_income_posts(monkeypatch):
    # The live symptom: realized_pnl_usd 0.0 alongside commission -3.33 (entry-side only) while
    # the paper book showed a real win. The close-side rows simply had not posted yet.
    from ats.execution import executor

    monkeypatch.setattr(executor.asyncio, "sleep", _no_sleep)
    client = _IncomeClient(3, {"realized_pnl": 78.51, "commission": -10.09, "funding": 0.0})
    costs = await executor._realized_costs_settled(
        client, "SOLUSDT", 0, expect_nonzero=True
    )
    assert costs["realized_pnl"] == 78.51
    assert client.calls == 3


@pytest.mark.asyncio
async def test_realized_costs_no_retry_for_a_genuinely_flat_trade():
    # A trade that really did net zero must not pay three sleeps to confirm it.
    from ats.execution import executor

    client = _IncomeClient(99, {})
    costs = await executor._realized_costs_settled(
        client, "SOLUSDT", 0, expect_nonzero=False
    )
    assert costs["realized_pnl"] == 0.0
    assert client.calls == 1


@pytest.mark.asyncio
async def test_realized_costs_gives_up_and_returns_zero(monkeypatch):
    from ats.execution import executor

    monkeypatch.setattr(executor.asyncio, "sleep", _no_sleep)
    client = _IncomeClient(99, {})
    costs = await executor._realized_costs_settled(
        client, "SOLUSDT", 0, expect_nonzero=True
    )
    # Still zero after every retry — returned as-is, but logged as unsettled rather than silent.
    assert costs["realized_pnl"] == 0.0
    assert client.calls == 1 + len(executor._REALIZED_RETRY_DELAYS)
