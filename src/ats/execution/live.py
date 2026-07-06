"""Live testnet execution context — the mirror-model bridge.

When ``--live-execute`` is on, the CLI builds a :class:`LiveContext` (holding the testnet
client + a structured order-log writer) and installs it in the ``live_ctx`` ContextVar. The
paper executor (``execution.executor``) keeps writing ``paper_trades`` and ADDITIONALLY drives
the exchange through ``live_ctx``. To make live *achieve* the fills the sim assumes (rather than
mirror them one-way with market orders): entries rest as real LIMIT orders at the zone edge and
are booked on their actual fill (``place_entry_limit`` + the executor's pending machine), and
exits rest as native STOP_MARKET/TAKE_PROFIT_MARKET protection (``place_protection``) that the
deterministic exit machine cancels/replaces as the stop moves. Scale-outs and forced closes
still use reduce-only market orders.

``live_ctx`` is None on every non-live path (replay, tick, paper run), so the existing
behavior is byte-unchanged.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from ats.execution.binance_futures import BinanceFuturesTestnet, OrderError, OrderResult
from ats.logging import get_logger

log = get_logger(__name__)

# Binance: "Order would immediately trigger." Raised when a STOP_MARKET trigger sits on the wrong
# side of the current mark — i.e. the level is ALREADY breached, so the stop can't rest and the
# position should be flat now. On a stop RAISE (trail/breakeven) this means price has run back
# through the new stop; the safe response is to market-close, not to leave the position naked.
_ERR_WOULD_IMMEDIATELY_TRIGGER = -2021


def _side_for_open(direction: str) -> str:
    return "BUY" if direction == "long" else "SELL"


def _side_for_reduce(direction: str) -> str:
    # Reduce/close is the opposite side of the position.
    return "SELL" if direction == "long" else "BUY"


class LiveContext:
    """Holds the testnet client + order log for one live run. Methods mirror executor sites."""

    def __init__(self, client: BinanceFuturesTestnet, order_log_path: Path) -> None:
        self._client = client
        self._path = order_log_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: TextIO = self._path.open("a", encoding="utf-8")
        # Per-trade protective algo-order ids: trade_id -> {"sl": id, "tp": id, "direction": str}.
        # Engine-synced model: SL+TP are placed at entry as a safety net (closePosition=true) and
        # the deterministic exit machine cancels/replaces them as the stop moves / trade closes.
        self._protection: dict[str, dict[str, str]] = {}
        # Pending resting-limit ENTRIES, keyed by exchange order id -> booking payload (see
        # executor.open_paper_trade). A limit rests here until executor.poll_live_entries books it
        # on fill or drops it on the fill-or-cancel timeout — this is what makes the live entry a
        # real resting order (matching the sim) instead of a market-order mirror.
        self._pending: dict[str, dict[str, Any]] = {}
        # Last known USDT wallet balance, used to mark position sizing to the real account (Fix 4).
        # Seeded from the user-data ACCOUNT_UPDATE stream (live_ws) and refreshed on the poll
        # cadence via ``wallet_equity``. None until first fetched → sizing falls back to config.
        self._wallet_equity: float | None = None

    @property
    def client(self) -> BinanceFuturesTestnet:
        return self._client

    # --- wallet equity (mark sizing to the real account) ------------------------------

    def set_wallet_equity(self, value: float) -> None:
        """Cache the latest USDT wallet balance (from the ACCOUNT_UPDATE stream)."""
        self._wallet_equity = value

    async def wallet_equity(self, *, refresh: bool = True) -> float | None:
        """Return the USDT wallet balance to size on; refresh via REST unless disabled.

        Falls back to the last cached value on a failed/empty fetch (never raises), and to None
        if never seen — the caller then uses the config default, preserving old behavior.
        """
        if refresh:
            acct = await self._client.account_state()
            wb = acct.get("wallet_balance") if acct else None
            if wb:
                self._wallet_equity = float(wb)
        return self._wallet_equity

    # The recorder the client calls before/after every exchange interaction.
    def record(self, action: str, payload: dict[str, Any]) -> None:
        line = {"ts": datetime.now(UTC).isoformat(), "action": action, **payload}
        self._fh.write(json.dumps(line, default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    # --- mirror-order helpers (called from the executor seam) -------------------------

    async def place_entry_limit(
        self, symbol: str, direction: str, *, notional_usd: float, entry_price: float,
        leverage: float | None,
    ) -> OrderResult | None:
        """Set leverage + place a RESTING LIMIT entry at ``entry_price``. None on reject/too-small.

        The returned order is ``NEW`` (resting), not necessarily filled — the caller registers it
        as pending and ``executor.poll_live_entries`` resolves the fill. This is the entry half of
        "make live match sim": the sim assumes a fill at the zone edge, so we rest a real
        (maker-only) limit there rather than crossing the spread with a market order.
        """
        await self._client.load_filters(symbol)
        qty = self._client.round_qty(symbol, notional_usd / entry_price) if entry_price > 0 else 0.0
        if qty <= 0:
            self.record("open", {"symbol": symbol, "status": "skipped_below_min", "qty": qty})
            log.warning("live_open_below_min", symbol=symbol, notional=notional_usd)
            return None
        try:
            acct = await self._client.account_state()
            if acct:
                log.info(
                    "live_account_state",
                    symbol=symbol,
                    leverage=leverage,
                    notional_usd=notional_usd,
                    wallet_balance=acct.get("wallet_balance"),
                    available_balance=acct.get("available_balance"),
                )
            if leverage is not None:
                await self._client.set_leverage(symbol, int(leverage))
            return await self._client.limit_order(
                symbol, _side_for_open(direction), qty, entry_price
            )
        except OrderError as exc:
            log.warning(
                "live_open_rejected", symbol=symbol, qty=qty, notional=notional_usd, error=str(exc)
            )
            return None

    # --- pending resting-limit entry tracking -----------------------------------------

    def register_pending(self, key: str, record: dict[str, Any]) -> None:
        """Track a resting-limit entry (keyed by exchange order id) awaiting its fill."""
        self._pending[key] = record

    def pop_pending(self, key: str) -> dict[str, Any] | None:
        """Remove and return a pending entry (booked on fill or dropped on timeout)."""
        return self._pending.pop(key, None)

    def has_pending(self, symbol: str) -> bool:
        """True if a resting-limit entry for ``symbol`` is awaiting a fill (blocks new entries)."""
        return any(r["symbol"] == symbol for r in self._pending.values())

    def pending_snapshot(self, symbol: str) -> list[dict[str, Any]]:
        """A copy of the pending entries for ``symbol`` (safe to iterate while mutating)."""
        return [dict(r) for r in self._pending.values() if r["symbol"] == symbol]

    async def reduce_position(
        self, symbol: str, direction: str, *, notional_usd: float, entry_price: float, frac: float,
        intended_price: float | None = None,
    ) -> OrderResult | None:
        """Scale out ``frac`` of the original position with a reduce-only market order."""
        await self._client.load_filters(symbol)
        orig_qty = (notional_usd / entry_price) if entry_price > 0 else 0.0
        qty = self._client.round_qty(symbol, orig_qty * frac)
        if qty <= 0:
            return None
        try:
            return await self._client.market_order(
                symbol, _side_for_reduce(direction), qty, reduce_only=True,
                intended_price=intended_price,
            )
        except OrderError as exc:
            log.warning("live_reduce_rejected", symbol=symbol, error=str(exc))
            return None

    # --- protective SL+TP (OCO-like, engine-synced) -----------------------------------

    async def place_protection(
        self, trade_id: str, symbol: str, direction: str, *, stop_loss: float, take_profit: float
    ) -> None:
        """Place a STOP_MARKET (SL) + TAKE_PROFIT_MARKET (TP), both closePosition=true.

        Both close the whole remaining position; when either triggers, Binance auto-cancels the
        other (the futures equivalent of OCO). ``take_profit`` is the FINAL target — intermediate
        scale-outs are handled by the engine firing reduce-only market orders.
        """
        side = _side_for_reduce(direction)
        # Try once, then retry once on failure before giving up — a transient reject shouldn't
        # cost us a good entry. Only the FINAL failure flattens (never hold unprotected).
        last_exc: OrderError | None = None
        for attempt in range(2):
            prot: dict[str, str] = {"direction": direction}
            try:
                prot["sl"] = await self._client.place_conditional(
                    symbol, side, "STOP_MARKET", trigger_price=stop_loss, close_position=True
                )
                prot["tp"] = await self._client.place_conditional(
                    symbol, side, "TAKE_PROFIT_MARKET", trigger_price=take_profit,
                    close_position=True,
                )
                self._protection[trade_id] = prot
                log.info(
                    "live_protection_placed",
                    symbol=symbol,
                    trade_id=trade_id,
                    sl_id=prot.get("sl"),
                    sl_trigger=stop_loss,
                    tp_id=prot.get("tp"),
                    tp_trigger=take_profit,
                )
                return
            except OrderError as exc:
                last_exc = exc
                log.warning(
                    "live_protection_attempt_failed",
                    symbol=symbol, trade_id=trade_id, attempt=attempt, error=str(exc),
                )
                # An SL may have landed before the TP failed — cancel it so the retry starts clean.
                if prot.get("sl"):
                    await self._client.cancel_conditional(symbol, prot["sl"])
        # Both attempts failed: flatten immediately rather than hold an unprotected position.
        log.warning(
            "live_protection_failed",
            symbol=symbol,
            trade_id=trade_id,
            stop_loss=stop_loss,
            take_profit=take_profit,
            error=str(last_exc),
        )
        self.record("protect", {"trade_id": trade_id, "status": "failed_flattening"})
        await self.close_position(symbol, direction)

    async def replace_stop(
        self, trade_id: str, symbol: str, direction: str, new_stop: float
    ) -> None:
        """Move the protective stop to ``new_stop`` (trail / breakeven) without ever going naked.

        Place-new-BEFORE-cancel-old: the previous stop stays live until the replacement is
        confirmed, so a failed placement never leaves the position unprotected (the old bug
        cancelled first, then a -2021 on the place left a naked position). Two failure paths:
        - ``-2021`` on a stop RAISE = the new level is already breached, so the position should
          be flat NOW → market-close and let the next tick reconcile.
        - any other reject → keep the old stop untouched and log; the position stays protected
          at the prior level and the trail retries next tick.
        """
        prot = self._protection.get(trade_id)
        if prot is None:
            return
        old = prot.get("sl")
        try:
            new_sl = await self._client.place_conditional(
                symbol, _side_for_reduce(direction), "STOP_MARKET",
                trigger_price=new_stop, close_position=True,
            )
        except OrderError as exc:
            if exc.code == _ERR_WOULD_IMMEDIATELY_TRIGGER:
                # Trail already breached: flatten now (old stop still active until this closes).
                log.warning(
                    "live_stop_raise_breached",
                    symbol=symbol, trade_id=trade_id, new_stop=new_stop, error=str(exc),
                )
                self.record("replace_stop", {"trade_id": trade_id, "status": "breached_flattening"})
                await self.close_position(symbol, direction, intended_price=new_stop)
                return
            # Non-2021 reject: keep the existing stop; do NOT cancel it.
            log.warning(
                "live_replace_stop_failed",
                symbol=symbol, trade_id=trade_id, new_stop=new_stop,
                code=exc.code, error=str(exc),
            )
            return
        # New stop confirmed — now it's safe to cancel the previous one.
        if old:
            await self._client.cancel_conditional(symbol, old)
        prot["sl"] = new_sl
        self._protection[trade_id] = prot
        log.info(
            "live_stop_replaced",
            symbol=symbol,
            trade_id=trade_id,
            new_stop=new_stop,
            sl_id=new_sl,
        )

    async def cancel_protection(self, trade_id: str, symbol: str) -> None:
        """Cancel any remaining SL/TP for a trade (on close)."""
        prot = self._protection.pop(trade_id, None)
        if not prot:
            return
        for key in ("sl", "tp"):
            algo_id = prot.get(key)
            if algo_id:
                await self._client.cancel_conditional(symbol, algo_id)

    async def close_position(
        self, symbol: str, direction: str, *, intended_price: float | None = None
    ) -> OrderResult | None:
        """Close the FULL remaining exchange position (reduce-only), querying live size."""
        pos = await self._client.position_risk(symbol)
        if pos is None:
            self.record("close", {"symbol": symbol, "status": "already_flat"})
            return None
        amt = abs(float(pos.get("positionAmt", 0)))
        qty = self._client.round_qty(symbol, amt)
        if qty <= 0:
            return None
        try:
            return await self._client.market_order(
                symbol, _side_for_reduce(direction), qty, reduce_only=True,
                intended_price=intended_price,
            )
        except OrderError as exc:
            log.warning("live_close_rejected", symbol=symbol, error=str(exc))
            return None


# Installed by the CLI for the duration of a live run; None everywhere else.
live_ctx: ContextVar[LiveContext | None] = ContextVar("live_ctx", default=None)
