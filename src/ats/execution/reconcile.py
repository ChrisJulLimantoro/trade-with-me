"""Paper-trade reconciliation — pure exit logic over candles.

Given an open trade and the candles that followed its entry, decide if/when/why it
closed. Conservative rule: if a single candle's range straddles BOTH the stop and the
take-profit, assume the STOP filled first (worst case). Exit priority per bar:
hard-invalidation → stop → take-profit → expiry.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any


@dataclass
class ExitResult:
    exit_price: float
    exit_time: datetime
    exit_reason: str  # sl|tp|expiry|invalidation|breakeven|trail
    pnl_pct: float  # signed return on the FULL position (sums all scaled-out legs)


def _pnl_pct(direction: str, entry: float, exit_price: float) -> float:
    if entry == 0:
        return 0.0
    raw = (exit_price - entry) / entry
    return raw if direction == "long" else -raw


def _liq_hit(direction: str, candle_high: float, candle_low: float, liq_price: float) -> bool:
    return candle_low <= liq_price if direction == "long" else candle_high >= liq_price


def check_bar_exit(
    trade: dict[str, Any],
    candle: dict[str, Any],
    *,
    expires_at: datetime | None,
    hard_invalidation: bool = False,
    liq_price: float | None = None,
) -> ExitResult | None:
    """Decide whether a single candle closes the trade. Returns None if still open.

    ``trade`` needs direction, entry_price, stop_loss, take_profit. ``candle`` needs
    open_time, high, low, close. ``liq_price`` (if given) is a hard liquidation backstop.
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
    if liq_price is not None and _liq_hit(direction, high, low, liq_price):
        return ExitResult(liq_price, ts, "liquidation", _pnl_pct(direction, entry, liq_price))
    if tp_hit:
        return ExitResult(tp, ts, "tp", _pnl_pct(direction, entry, tp))
    if expires_at is not None and ts >= expires_at:  # None = time-stop disabled
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


# --------------------------------------------------------------------------------------
# Scale-out / breakeven / trailing exit management
#
# ``step_trade`` is the stateful single-bar primitive used by the live/replay engine. It
# generalizes ``check_bar_exit``: instead of one full close at ``take_profit[0]``, it
# scales out a fraction at each non-final target, moves the stop to breakeven after the
# first partial, and trails the runner so winners can exceed the nearest target while
# losers (post-breakeven) can no longer turn into a loss. The carried ``TradeState`` is
# persisted between bars (in ``PaperTrade.trade_metadata``).
# --------------------------------------------------------------------------------------


@dataclass
class TradeState:
    """Mutable per-trade exit state carried across bars."""

    remaining_frac: float = 1.0  # fraction of the original position still open
    realized_pnl_pct: float = 0.0  # position-weighted pnl banked from scaled-out legs
    working_stop: float | None = None  # current stop; None → use the trade's stop_loss
    tp_index: int = 0  # index of the next take-profit to target
    breakeven: bool = False  # stop has been moved to (at least) entry

    @classmethod
    def from_metadata(cls, md: dict[str, Any] | None) -> "TradeState":
        md = md or {}
        ws = md.get("stop_working")
        return cls(
            remaining_frac=float(md.get("remaining_frac", 1.0)),
            realized_pnl_pct=float(md.get("realized_pnl_pct", 0.0)),
            working_stop=float(ws) if ws is not None else None,
            tp_index=int(md.get("tp_index", 0)),
            breakeven=bool(md.get("breakeven", False)),
        )

    def to_metadata(self) -> dict[str, Any]:
        """The keys to merge back into ``trade_metadata`` (alongside ``expires_at``)."""
        return {
            "remaining_frac": self.remaining_frac,
            "realized_pnl_pct": self.realized_pnl_pct,
            "stop_working": self.working_stop,
            "tp_index": self.tp_index,
            "breakeven": self.breakeven,
        }


@dataclass
class PartialFill:
    price: float
    time: datetime
    frac: float  # fraction of the FULL position closed at this leg
    pnl_pct: float  # leg price return (unweighted)
    tp_index: int


@dataclass
class BarStep:
    state: TradeState
    exit_result: ExitResult | None = None  # set when the trade fully closes this bar
    partial: PartialFill | None = None  # set when a scale-out leg fills this bar
    notes: list[str] = field(default_factory=list)

    @property
    def closed(self) -> bool:
        return self.exit_result is not None


def step_trade(
    trade: dict[str, Any],
    candle: dict[str, Any],
    state: TradeState,
    *,
    expires_at: datetime | None,
    scale_out_frac: float,
    trail_atr_mult: float = 0.0,
    atr: float | None = None,
    hard_invalidation: bool = False,
    liq_price: float | None = None,
) -> BarStep:
    """Advance one open trade by a single bar. Returns the resulting :class:`BarStep`.

    Exit priority per bar: hard-invalidation → working stop → liquidation → current
    take-profit → expiry (skipped for breakeven-protected runners) → trail update.

    ``liq_price`` is a hard backstop: sizing guarantees the stop sits inside it, so the
    stop normally fills first; liquidation only fires if the working stop is somehow looser.
    """
    direction = trade["direction"]
    entry = float(trade["entry_price"])
    tps = [float(x) for x in trade["take_profit"]]
    full_stop = float(trade["stop_loss"])
    working_stop = state.working_stop if state.working_stop is not None else full_stop
    tp_idx = min(state.tp_index, len(tps) - 1)
    target = tps[tp_idx]
    is_final_tp = tp_idx >= len(tps) - 1
    ts = candle["open_time"]
    high, low, close = float(candle["high"]), float(candle["low"]), float(candle["close"])

    def pnl(px: float) -> float:
        return _pnl_pct(direction, entry, px)

    def close_all(px: float, reason: str) -> BarStep:
        total = state.realized_pnl_pct + state.remaining_frac * pnl(px)
        new = replace(state, remaining_frac=0.0, realized_pnl_pct=total)
        return BarStep(state=new, exit_result=ExitResult(px, ts, reason, total))

    if hard_invalidation:
        return close_all(close, "invalidation")

    if direction == "long":
        sl_hit, tp_hit = low <= working_stop, high >= target
    else:
        sl_hit, tp_hit = high >= working_stop, low <= target

    # Conservative: a bar that straddles both the stop and the target stops out first.
    if sl_hit:
        if state.breakeven and abs(working_stop - entry) < 1e-9:
            reason = "breakeven"
        elif pnl(working_stop) > 0:
            reason = "trail"
        else:
            reason = "sl"
        return close_all(working_stop, reason)

    if liq_price is not None and _liq_hit(direction, high, low, liq_price):
        return close_all(liq_price, "liquidation")

    if tp_hit:
        if is_final_tp:
            return close_all(target, "tp")
        # Partial scale-out: bank a fraction, move stop to breakeven, advance the target.
        close_frac = state.remaining_frac * scale_out_frac
        realized = state.realized_pnl_pct + close_frac * pnl(target)
        new = TradeState(
            remaining_frac=state.remaining_frac - close_frac,
            realized_pnl_pct=realized,
            working_stop=entry,  # breakeven
            tp_index=tp_idx + 1,
            breakeven=True,
        )
        return BarStep(
            state=new,
            partial=PartialFill(target, ts, close_frac, pnl(target), tp_idx),
            notes=[f"scaled out {close_frac:.2f} at tp{tp_idx + 1}; stop→breakeven"],
        )

    # Time-stop: let breakeven-protected runners keep going; close anything still at risk.
    # expires_at is None when the time-stop is disabled (settings.max_hold_bars <= 0).
    if expires_at is not None and ts >= expires_at and not state.breakeven:
        return close_all(close, "expiry")

    # Trail the runner once it is protected.
    new_state = state
    if state.breakeven and trail_atr_mult and atr:
        if direction == "long":
            ws = max(working_stop, close - trail_atr_mult * atr)
        else:
            ws = min(working_stop, close + trail_atr_mult * atr)
        new_state = replace(state, working_stop=ws)
    return BarStep(state=new_state)
