"""Paper-trade reconciliation — pure exit logic over candles.

Given an open trade and the candles that followed its entry, decide if/when/why it
closed. Conservative rule: if a single candle's range straddles BOTH the stop and the
take-profit, assume the STOP filled first (worst case). Exit priority per bar:
hard-invalidation → stop → take-profit → expiry.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class ExitResult:
    exit_price: float
    exit_time: datetime
    exit_reason: str  # sl|tp|expiry|invalidation
    pnl_pct: float  # signed price return on the position


def _pnl_pct(direction: str, entry: float, exit_price: float) -> float:
    if entry == 0:
        return 0.0
    raw = (exit_price - entry) / entry
    return raw if direction == "long" else -raw


def check_bar_exit(
    trade: dict[str, Any],
    candle: dict[str, Any],
    *,
    expires_at: datetime,
    hard_invalidation: bool = False,
) -> ExitResult | None:
    """Decide whether a single candle closes the trade. Returns None if still open.

    ``trade`` needs direction, entry_price, stop_loss, take_profit. ``candle`` needs
    open_time, high, low, close.
    """
    direction = trade["direction"]
    entry = float(trade["entry_price"])
    stop = float(trade["stop_loss"])
    tp = float(trade["take_profit"][0])  # nearest target = full close (POC)
    ts = candle["open_time"]
    high, low, close = float(candle["high"]), float(candle["low"]), float(candle["close"])

    if hard_invalidation:
        return ExitResult(close, ts, "invalidation", _pnl_pct(direction, entry, close))

    if direction == "long":
        sl_hit, tp_hit = low <= stop, high >= tp
    else:
        sl_hit, tp_hit = high >= stop, low <= tp

    if sl_hit:  # conservative: stop wins ties
        return ExitResult(stop, ts, "sl", _pnl_pct(direction, entry, stop))
    if tp_hit:
        return ExitResult(tp, ts, "tp", _pnl_pct(direction, entry, tp))
    if ts >= expires_at:
        return ExitResult(close, ts, "expiry", _pnl_pct(direction, entry, close))
    return None


def reconcile_trade(
    trade: dict[str, Any],
    future_candles: Iterable[dict[str, Any]],
    *,
    expires_at: datetime,
    invalidation_hits: frozenset[datetime] = frozenset(),
) -> ExitResult | None:
    """Walk candles after entry in order; return the first exit, or None if still open.

    ``invalidation_hits`` is the set of candle open_times at which a HARD invalidation
    fired (computed elsewhere from features); such a bar closes the trade.
    """
    for candle in future_candles:
        result = check_bar_exit(
            trade,
            candle,
            expires_at=expires_at,
            hard_invalidation=candle["open_time"] in invalidation_hits,
        )
        if result is not None:
            return result
    return None
