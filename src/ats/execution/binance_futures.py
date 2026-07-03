"""Binance Futures TESTNET client — the ONLY component that places real orders.

Thin async wrapper over ``python-binance`` ``AsyncClient`` (``testnet=True``), which handles
HMAC signing, timestamps, recvWindow, and the testnet base URLs. This is testnet-only by
construction: callers (the CLI guard) refuse to instantiate it unless ``binance_testnet`` is set.

Every exchange interaction is passed to an optional ``recorder`` callback BEFORE the request is
sent and AFTER the response/error, so the structured order log captures attempts even on reject
or crash (see ``execution.executor`` / the live order jsonl).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

from binance import AsyncClient
from binance.exceptions import BinanceAPIException

from ats.logging import get_logger

log = get_logger(__name__)

# Callback signature: ``recorder(action: str, payload: dict) -> None``.
Recorder = Callable[[str, dict[str, Any]], None]


class OrderError(RuntimeError):
    """A testnet order/REST call was rejected. Callers log and skip (never open on failure)."""


@dataclass(frozen=True)
class SymbolFilters:
    """Trading filters for one symbol, used to round quantities to an acceptable order."""

    step_size: float
    min_qty: float
    tick_size: float
    min_notional: float
    qty_precision: int


@dataclass(frozen=True)
class OrderResult:
    """Normalized result of a placed market order."""

    order_id: str
    side: str
    qty: float
    avg_price: float
    status: str
    raw: dict[str, Any]


class BinanceFuturesTestnet:
    """Signed testnet client. Construct via ``await BinanceFuturesTestnet.create(...)``."""

    def __init__(self, client: AsyncClient, recorder: Recorder | None = None) -> None:
        self._client = client
        self._recorder = recorder
        self._filters: dict[str, SymbolFilters] = {}

    @classmethod
    async def create(
        cls,
        api_key: str,
        api_secret: str,
        *,
        testnet: bool = True,
        futures_url: str | None = None,
        recorder: Recorder | None = None,
    ) -> "BinanceFuturesTestnet":
        if not testnet:
            # Defense-in-depth: this feature is testnet-only. The CLI guard refuses mainnet too.
            raise OrderError("BinanceFuturesTestnet refuses to run against mainnet (testnet only).")
        client = await AsyncClient.create(api_key, api_secret, testnet=testnet)
        if futures_url:
            # Point futures REST at a custom testnet host (e.g. https://demo-fapi.binance.com).
            client.FUTURES_URL = futures_url.rstrip("/")
        return cls(client, recorder)

    @property
    def raw(self) -> AsyncClient:
        """The underlying python-binance AsyncClient (for BinanceSocketManager)."""
        return self._client

    async def close(self) -> None:
        await self._client.close_connection()

    def _record(self, action: str, payload: dict[str, Any]) -> None:
        if self._recorder is not None:
            try:
                self._recorder(action, payload)
            except Exception:  # never let logging break execution
                log.warning("order_recorder_failed", action=action)

    # --- filters / sizing -------------------------------------------------------------

    async def load_filters(self, symbol: str) -> SymbolFilters:
        """Fetch and cache LOT_SIZE / PRICE_FILTER / MIN_NOTIONAL for ``symbol``."""
        if symbol in self._filters:
            return self._filters[symbol]
        info = await self._client.futures_exchange_info()
        sym = next((s for s in info["symbols"] if s["symbol"] == symbol), None)
        if sym is None:
            raise OrderError(f"symbol {symbol} not found in futures_exchange_info")
        fmap = {f["filterType"]: f for f in sym["filters"]}
        lot = fmap.get("LOT_SIZE", {})
        price = fmap.get("PRICE_FILTER", {})
        # MIN_NOTIONAL filter type is "MIN_NOTIONAL" on futures.
        notional = fmap.get("MIN_NOTIONAL", {})
        step = float(lot.get("stepSize", "0.001"))
        filters = SymbolFilters(
            step_size=step,
            min_qty=float(lot.get("minQty", step)),
            tick_size=float(price.get("tickSize", "0.01")),
            min_notional=float(notional.get("notional", "0")),
            qty_precision=int(sym.get("quantityPrecision", 3)),
        )
        self._filters[symbol] = filters
        return filters

    def round_qty(self, symbol: str, qty: float) -> float:
        """Round ``qty`` DOWN to the symbol's stepSize. Returns 0.0 if below minQty."""
        f = self._filters.get(symbol)
        if f is None:
            raise OrderError(f"filters not loaded for {symbol}; call load_filters first")
        if f.step_size <= 0:
            return round(qty, f.qty_precision)
        steps = math.floor(qty / f.step_size)
        rounded = round(steps * f.step_size, f.qty_precision)
        return rounded if rounded >= f.min_qty else 0.0

    def round_price(self, symbol: str, price: float) -> float:
        """Snap ``price`` to the symbol's tickSize (NEAREST tick).

        Conditional orders (STOP_MARKET / TAKE_PROFIT_MARKET trigger, trailing activation) are
        rejected with ``-1111 Precision is over the maximum`` if the price carries more decimals
        than the tick allows. A stop/TP trigger should land on the closest valid tick, so we round
        to nearest rather than floor (unlike ``round_qty``, which must floor to stay affordable).
        """
        f = self._filters.get(symbol)
        if f is None:
            raise OrderError(f"filters not loaded for {symbol}; call load_filters first")
        if f.tick_size <= 0:
            return price
        # Decimal places implied by the tick (e.g. 0.01 -> 2, 0.5 -> 1, 1 -> 0).
        precision = max(0, -int(math.floor(math.log10(f.tick_size))))
        return round(round(price / f.tick_size) * f.tick_size, precision)

    # --- account state (tracker / reconcile) ------------------------------------------

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        lev = max(1, int(round(leverage)))
        self._record("set_leverage", {"symbol": symbol, "leverage": lev})
        try:
            await self._client.futures_change_leverage(symbol=symbol, leverage=lev)
        except BinanceAPIException as exc:
            self._record("set_leverage", {"symbol": symbol, "leverage": lev, "error": str(exc)})
            raise OrderError(f"set_leverage failed: {exc}") from exc

    async def position_risk(self, symbol: str) -> dict[str, Any] | None:
        """Return the open position for ``symbol`` (positionAmt != 0), else None."""
        rows = await self._client.futures_position_information(symbol=symbol)
        for row in rows:
            if abs(float(row.get("positionAmt", 0))) > 0:
                return row
        return None

    async def open_orders(self, symbol: str) -> list[dict[str, Any]]:
        return await self._client.futures_get_open_orders(symbol=symbol)

    # --- order placement --------------------------------------------------------------

    async def market_order(
        self, symbol: str, side: str, qty: float, *, reduce_only: bool = False
    ) -> OrderResult:
        """Place a MARKET order. ``side`` is 'BUY'/'SELL'. Returns the normalized fill."""
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": qty,
            "newOrderRespType": "RESULT",
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        action = "close" if reduce_only else "open"
        self._record(action, {"request": params, "status": "submitting"})
        try:
            resp = await self._client.futures_create_order(**params)
        except BinanceAPIException as exc:
            self._record(action, {"request": params, "status": "rejected", "error": str(exc)})
            raise OrderError(f"market_order {side} {qty} {symbol} rejected: {exc}") from exc
        avg = float(resp.get("avgPrice") or 0.0)
        if avg == 0.0:
            executed = float(resp.get("executedQty") or 0.0)
            quote = float(resp.get("cumQuote") or 0.0)
            avg = quote / executed if executed > 0 else 0.0
        result = OrderResult(
            order_id=str(resp.get("orderId", "")),
            side=side,
            qty=float(resp.get("executedQty") or qty),
            avg_price=avg,
            status=str(resp.get("status", "")),
            raw=resp,
        )
        self._record(
            action,
            {
                "request": params,
                "status": result.status,
                "order_id": result.order_id,
                "fill_price": result.avg_price,
            },
        )
        log.info(
            "testnet_order",
            action=action,
            symbol=symbol,
            side=side,
            qty=result.qty,
            order_id=result.order_id,
            fill=result.avg_price,
            status=result.status,
        )
        return result

    # --- conditional (algo) orders: SL / TP / trailing ---------------------------------
    # Binance migrated conditional orders OFF /fapi/v1/order to POST /fapi/v1/algoOrder
    # (algoType=CONDITIONAL). The old endpoint now rejects them with error -4120.

    async def place_conditional(
        self,
        symbol: str,
        side: str,
        order_type: str,
        *,
        trigger_price: float | None = None,
        close_position: bool = False,
        reduce_only: bool = False,
        quantity: float | None = None,
        callback_rate: float | None = None,
        activate_price: float | None = None,
        working_type: str = "MARK_PRICE",
    ) -> str:
        """Place a CONDITIONAL algo order (STOP_MARKET / TAKE_PROFIT_MARKET / TRAILING_STOP_MARKET).

        Returns the exchange ``algoId``. ``close_position`` (close-all) cannot be combined with
        ``quantity``/``reduce_only``. ``callback_rate`` is a percent in [0.1, 10] (trailing only).
        """
        params: dict[str, Any] = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "workingType": working_type,
        }
        if trigger_price is not None:
            params["triggerPrice"] = self.round_price(symbol, trigger_price)
        if close_position:
            params["closePosition"] = "true"
        else:
            if quantity is not None:
                params["quantity"] = quantity
            if reduce_only:
                params["reduceOnly"] = "true"
        if callback_rate is not None:
            params["callbackRate"] = callback_rate
        if activate_price is not None:
            params["activatePrice"] = self.round_price(symbol, activate_price)
        self._record("protect", {"request": params, "status": "submitting"})
        try:
            resp = await self._client.futures_create_algo_order(**params)
        except BinanceAPIException as exc:
            self._record("protect", {"request": params, "status": "rejected", "error": str(exc)})
            raise OrderError(f"conditional {order_type} {symbol} rejected: {exc}") from exc
        algo_id = str(resp.get("algoId") or resp.get("orderId") or "")
        self._record(
            "protect",
            {"request": params, "status": resp.get("status", "NEW"), "algo_id": algo_id},
        )
        log.info("testnet_conditional", symbol=symbol, type=order_type, algo_id=algo_id)
        return algo_id

    async def cancel_conditional(self, symbol: str, algo_id: str) -> None:
        """Cancel a single conditional algo order. Non-fatal: a gone order is logged, not raised."""
        self._record("cancel_protect", {"symbol": symbol, "algo_id": algo_id})
        try:
            await self._client.futures_cancel_algo_order(symbol=symbol, algoId=algo_id)
        except BinanceAPIException as exc:
            # The order may already be filled/cancelled (e.g. SL triggered) — that's fine.
            log.info("conditional_cancel_noop", symbol=symbol, algo_id=algo_id, error=str(exc))
            self._record("cancel_protect", {"symbol": symbol, "algo_id": algo_id, "noop": str(exc)})

    async def open_algo_orders(self, symbol: str) -> list[dict[str, Any]]:
        return await self._client.futures_get_open_algo_orders(symbol=symbol)
