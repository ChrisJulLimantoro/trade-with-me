"""Live↔sim gap fixes: stop-safety (replace_stop), resting-limit entry primitive, and the
pending-entry fill/timeout state machine. All pure-logic with fakes — no network, no DB.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest

from ats.execution.binance_futures import OrderError, OrderResult
from ats.execution.executor import poll_live_entries
from ats.execution.live import LiveContext, live_ctx

# --------------------------------------------------------------------------------------
# Fixtures / fakes
# --------------------------------------------------------------------------------------


class _FakeClient:
    """Minimal stand-in for BinanceFuturesTestnet used by LiveContext methods."""

    def __init__(self) -> None:
        self.cancelled_conditionals: list[str] = []
        self.placed_conditionals: list[dict] = []
        self.market_orders: list[dict] = []
        self.cancelled_orders: list[str] = []
        self.get_order_result: dict = {}
        self.position: dict | None = {"positionAmt": "1.0"}
        # place_conditional behavior: None = succeed; else an OrderError to raise once.
        self._raise_once: OrderError | None = None

    def raise_conditional_once(self, exc: OrderError) -> None:
        self._raise_once = exc

    async def place_conditional(self, symbol, side, order_type, **kw):
        if self._raise_once is not None:
            exc, self._raise_once = self._raise_once, None
            raise exc
        self.placed_conditionals.append({"type": order_type, **kw})
        return f"algo{len(self.placed_conditionals)}"

    async def cancel_conditional(self, symbol, algo_id):
        self.cancelled_conditionals.append(algo_id)

    async def position_risk(self, symbol):
        return self.position

    def round_qty(self, symbol, qty):
        return qty

    async def market_order(self, symbol, side, qty, *, reduce_only=False, intended_price=None):
        self.market_orders.append(
            {"symbol": symbol, "side": side, "qty": qty, "reduce_only": reduce_only}
        )
        return OrderResult("mkt1", side, qty, intended_price or 0.0, "FILLED", {})

    async def get_order(self, symbol, order_id):
        return self.get_order_result

    async def cancel_order(self, symbol, order_id):
        self.cancelled_orders.append(order_id)


def _ctx(client: _FakeClient) -> LiveContext:
    ctx = LiveContext.__new__(LiveContext)
    ctx._client = client
    ctx._protection = {}
    ctx._pending = {}
    ctx._fh = io.StringIO()  # record() writes here
    return ctx


class _FakeSession:
    """Records added ORM objects; async flush is a no-op."""

    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


# --------------------------------------------------------------------------------------
# Fix 1 — replace_stop must never leave the position naked
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_stop_2021_flattens_and_keeps_old_stop():
    client = _FakeClient()
    client.raise_conditional_once(OrderError("would immediately trigger", code=-2021))
    ctx = _ctx(client)
    ctx._protection["t1"] = {"sl": "old_sl", "tp": "tp1", "direction": "long"}

    await ctx.replace_stop("t1", "SOLUSDT", "long", 105.0)

    # Old stop must NOT be cancelled (the -2021 place failed), and we must have market-closed.
    assert "old_sl" not in client.cancelled_conditionals
    assert ctx._protection["t1"]["sl"] == "old_sl"  # still the live stop
    assert len(client.market_orders) == 1  # flattened
    assert client.market_orders[0]["reduce_only"] is True


@pytest.mark.asyncio
async def test_replace_stop_generic_reject_keeps_old_stop_no_close():
    client = _FakeClient()
    client.raise_conditional_once(OrderError("transient -1111", code=-1111))
    ctx = _ctx(client)
    ctx._protection["t1"] = {"sl": "old_sl", "tp": "tp1", "direction": "long"}

    await ctx.replace_stop("t1", "SOLUSDT", "long", 105.0)

    assert "old_sl" not in client.cancelled_conditionals  # old stop retained
    assert ctx._protection["t1"]["sl"] == "old_sl"
    assert client.market_orders == []  # NON-2021 reject must not flatten


@pytest.mark.asyncio
async def test_replace_stop_success_cancels_old_after_new_confirmed():
    client = _FakeClient()
    ctx = _ctx(client)
    ctx._protection["t1"] = {"sl": "old_sl", "tp": "tp1", "direction": "long"}

    await ctx.replace_stop("t1", "SOLUSDT", "long", 105.0)

    # New placed first, THEN old cancelled; protection now points at the new id.
    assert client.cancelled_conditionals == ["old_sl"]
    assert ctx._protection["t1"]["sl"] == "algo1"
    assert client.market_orders == []


# --------------------------------------------------------------------------------------
# Fix 2a — limit_order is a maker-only resting order at the snapped price
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_limit_order_is_post_only_and_snapped():
    from ats.execution.binance_futures import BinanceFuturesTestnet, SymbolFilters

    class _Raw:
        def __init__(self):
            self.params = None

        async def futures_create_order(self, **params):
            self.params = params
            return {"orderId": 7, "status": "NEW", "origQty": params["quantity"]}

    c = BinanceFuturesTestnet.__new__(BinanceFuturesTestnet)
    c._client = _Raw()
    c._recorder = None
    c._filters = {
        "SOLUSDT": SymbolFilters(
            step_size=0.001, min_qty=0.001, tick_size=0.01, min_notional=5.0, qty_precision=3
        )
    }
    res = await c.limit_order("SOLUSDT", "BUY", 2.0, 80.79546042)
    assert res.status == "NEW"
    assert res.order_id == "7"
    assert c._client.params["type"] == "LIMIT"
    assert c._client.params["timeInForce"] == "GTX"  # post-only (maker guaranteed)
    assert c._client.params["price"] == 80.80  # snapped to tick


# --------------------------------------------------------------------------------------
# Fix 2b — pending-entry state machine
# --------------------------------------------------------------------------------------


def _pending_record(**over) -> dict:
    rec = {
        "order_id": "o1",
        "symbol": "SOLUSDT",
        "direction": "long",
        "target_qty": 2.0,
        "limit_price": 80.0,
        "expires_at": None,
        "setup_id": "s1",
        "plan_id": "p1",
        "size_pct": 0.5,
        "leverage": 20.0,
        "margin_usd": 100.0,
        "notional_usd": 2000.0,
        "liq_price": 70.0,
        "risk_usd": 10.0,
        "entry_time": datetime(2026, 7, 4, tzinfo=UTC),
        "stop_loss": 78.0,
        "take_profit": [82.0, 84.0],
        "reasons": ["detected"],
        "trade_metadata": {"exit_mode": "trend"},
        "run_id": None,
        "run_label": None,
        "config_hash": None,
    }
    rec.update(over)
    return rec


@pytest.mark.asyncio
async def test_pending_fill_books_trade_at_real_fill_price():
    client = _FakeClient()
    client.get_order_result = {"status": "FILLED", "executedQty": "2.0", "avgPrice": "79.95"}
    ctx = _ctx(client)
    ctx.register_pending("o1", _pending_record())
    session = _FakeSession()

    token = live_ctx.set(ctx)
    try:
        await poll_live_entries(session, "SOLUSDT", datetime(2026, 7, 4, 1, tzinfo=UTC))
    finally:
        live_ctx.reset(token)

    assert len(session.added) == 1
    trade = session.added[0]
    assert trade.venue == "binance_testnet"
    assert float(trade.entry_price) == 80.0  # simulated zone edge (analytics source of truth)
    assert float(trade.entry_fill_price) == 79.95  # real fill (reality track)
    assert trade.status == "open"
    # Native protection placed (SL + final TP).
    assert len(client.placed_conditionals) == 2
    assert not ctx.has_pending("SOLUSDT")  # consumed


@pytest.mark.asyncio
async def test_pending_timeout_cancels_and_books_no_trade():
    client = _FakeClient()
    client.get_order_result = {"status": "NEW", "executedQty": "0", "avgPrice": "0"}
    ctx = _ctx(client)
    expired = datetime(2026, 7, 4, tzinfo=UTC)
    ctx.register_pending("o1", _pending_record(expires_at=expired))
    session = _FakeSession()

    token = live_ctx.set(ctx)
    try:
        # now is past expiry → cancel, no fill
        await poll_live_entries(session, "SOLUSDT", expired + timedelta(minutes=30))
    finally:
        live_ctx.reset(token)

    assert client.cancelled_orders == ["o1"]
    assert session.added == []  # no trade booked
    assert not ctx.has_pending("SOLUSDT")


@pytest.mark.asyncio
async def test_pending_still_resting_within_window_keeps_waiting():
    client = _FakeClient()
    client.get_order_result = {"status": "NEW", "executedQty": "0", "avgPrice": "0"}
    ctx = _ctx(client)
    expires = datetime(2026, 7, 4, 2, tzinfo=UTC)
    ctx.register_pending("o1", _pending_record(expires_at=expires))
    session = _FakeSession()

    token = live_ctx.set(ctx)
    try:
        await poll_live_entries(session, "SOLUSDT", datetime(2026, 7, 4, 1, tzinfo=UTC))
    finally:
        live_ctx.reset(token)

    assert client.cancelled_orders == []
    assert session.added == []
    assert ctx.has_pending("SOLUSDT")  # still resting


@pytest.mark.asyncio
async def test_pending_partial_fill_scales_sizing():
    client = _FakeClient()
    client.get_order_result = {"status": "FILLED", "executedQty": "1.0", "avgPrice": "80.0"}
    ctx = _ctx(client)
    ctx.register_pending("o1", _pending_record(target_qty=2.0))
    session = _FakeSession()

    token = live_ctx.set(ctx)
    try:
        await poll_live_entries(session, "SOLUSDT", datetime(2026, 7, 4, 1, tzinfo=UTC))
    finally:
        live_ctx.reset(token)

    trade = session.added[0]
    # 1.0 of 2.0 filled → half the notional/margin/risk booked.
    assert float(trade.notional_usd) == 1000.0
    assert float(trade.margin_usd) == 50.0
    assert float(trade.risk_usd) == 5.0


@pytest.mark.asyncio
async def test_poll_is_noop_without_live_ctx():
    session = _FakeSession()
    # live_ctx default is None (replay/paper) → poll must do nothing and not raise.
    await poll_live_entries(session, "SOLUSDT", datetime(2026, 7, 4, tzinfo=UTC))
    assert session.added == []
