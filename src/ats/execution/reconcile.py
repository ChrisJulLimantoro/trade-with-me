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
    pnl_pct: float  # internal signed return on notional (sums all scaled-out legs)


def _pnl_pct(direction: str, entry: float, exit_price: float) -> float:
    if entry == 0:
        return 0.0
    raw = (exit_price - entry) / entry
    return raw if direction == "long" else -raw


def net_of_costs(frac: float, gross_pnl: float, cost_bps: float) -> float:
    """Position-weighted pnl for a leg of size ``frac`` after round-trip trading costs.

    A leg pays ``cost_bps`` on entry and again on exit, so the round-trip cost in
    position-weighted terms is ``frac * 2 * cost_bps/10_000``. Returns the leg's
    contribution to total pnl (``frac * gross_pnl``) net of that cost. ``cost_bps=0``
    is a no-op, preserving the cost-free golden-value tests.
    """
    return frac * gross_pnl - frac * 2.0 * cost_bps / 10_000.0


def breakeven_stop(direction: str, entry: float, cost_bps: float) -> float:
    """The stop price that nets ~zero after round-trip costs (a true breakeven).

    A stop exactly at ``entry`` still loses the round-trip fee, so a trade that arms
    breakeven and immediately retraces dies at ``-2*cost_bps``. Shifting the breakeven
    stop just past entry (up for longs, down for shorts) by the round-trip cost makes a
    stop-out there net flat instead of a fee-only loss. ``cost_bps=0`` → returns ``entry``.
    """
    rt = 2.0 * cost_bps / 10_000.0
    return entry * (1.0 + rt) if direction == "long" else entry * (1.0 - rt)


def profit_lock_stop(
    direction: str, entry: float, cost_bps: float, lock_dist: float
) -> float:
    """A protective stop locked ``lock_dist`` (price units) of profit beyond cost-breakeven.

    The cost-breakeven nets ~zero; adding ``lock_dist`` past it (up for longs, down for
    shorts) means a trade that arms protection and then retraces exits at a small REAL gain
    instead of flat — turning a scratch (a non-win) into a win. ``lock_dist=0`` collapses to
    :func:`breakeven_stop`.
    """
    be = breakeven_stop(direction, entry, cost_bps)
    return be + lock_dist if direction == "long" else be - lock_dist


def _liq_hit(direction: str, candle_high: float, candle_low: float, liq_price: float) -> bool:
    return candle_low <= liq_price if direction == "long" else candle_high >= liq_price


def _trail_anchor(mode: str, direction: str, close: float, extreme: float | None) -> float:
    """Price the trailing stop is measured from.

    ``"close"`` is the legacy bar-close anchor: the stop trails the current close.
    ``"chandelier"`` anchors off the high-water extreme since entry (highest high for longs /
    lowest low for shorts), so a winner keeps the room it earned through a pullback. Falls back
    to ``close`` until the extreme is seeded. The ``max``/``min`` ratchet in the caller still
    guarantees the stop only ever tightens, regardless of mode. New modes extend this resolver
    without touching ``step_trade``'s flow (the ``direction`` arg is reserved for that).
    """
    if mode == "chandelier" and extreme is not None:
        return extreme
    return close


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
    trail_armed: bool = False  # pre-TP1 ATR trailing has armed
    max_favorable_pnl_pct: float = 0.0  # best signed return seen since entry
    max_adverse_pnl_pct: float = 0.0  # worst signed return seen since entry
    # High-water price extreme since entry — highest high (long) / lowest low (short). This is
    # the anchor a chandelier trail measures from. None until the first post-entry bar seeds it.
    extreme_favorable_px: float | None = None

    @classmethod
    def from_metadata(cls, md: dict[str, Any] | None) -> TradeState:
        md = md or {}
        ws = md.get("stop_working")
        ext = md.get("extreme_favorable_px")
        return cls(
            remaining_frac=float(md.get("remaining_frac", 1.0)),
            realized_pnl_pct=float(md.get("realized_pnl_pct", 0.0)),
            working_stop=float(ws) if ws is not None else None,
            tp_index=int(md.get("tp_index", 0)),
            breakeven=bool(md.get("breakeven", False)),
            trail_armed=bool(md.get("trail_armed", False)),
            max_favorable_pnl_pct=float(md.get("max_favorable_pnl_pct", 0.0)),
            max_adverse_pnl_pct=float(md.get("max_adverse_pnl_pct", 0.0)),
            extreme_favorable_px=float(ext) if ext is not None else None,
        )

    def to_metadata(self) -> dict[str, Any]:
        """The keys to merge back into ``trade_metadata`` (alongside ``expires_at``)."""
        return {
            "remaining_frac": self.remaining_frac,
            "realized_pnl_pct": self.realized_pnl_pct,
            "stop_working": self.working_stop,
            "tp_index": self.tp_index,
            "breakeven": self.breakeven,
            "trail_armed": self.trail_armed,
            "max_favorable_pnl_pct": self.max_favorable_pnl_pct,
            "max_adverse_pnl_pct": self.max_adverse_pnl_pct,
            "extreme_favorable_px": self.extreme_favorable_px,
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
    expiry_due: bool = False  # time-stop reached but deferred to a thesis review (caller decides)
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
    loss_cut_at: datetime | None = None,
    loss_cut_atr_mult: float = 0.0,
    trail_atr_mult: float = 0.0,
    trail_mode: str = "close",
    atr: float | None = None,
    early_stop_mode: str = "breakeven",
    early_trail_arm_atr: float = 0.0,
    early_trail_atr_mult: float = 0.0,
    breakeven_arm_atr: float = 0.0,
    breakeven_arm_cost_mult: float = 0.0,
    breakeven_lock_atr: float = 0.0,
    breakeven_requires_tp1: bool = False,
    trail_after_tp1_only: bool = False,
    cost_bps: float = 0.0,
    review_on_expiry: bool = False,
    hard_invalidation: bool = False,
    liq_price: float | None = None,
    stop_on_close: bool = False,
) -> BarStep:
    """Advance one open trade by a single bar. Returns the resulting :class:`BarStep`.

    Exit priority per bar: hard-invalidation → working stop → liquidation → current
    take-profit → early protection → expiry (skipped for protected runners and for
    in-profit trades) → post-TP1 trail update.

    ``early_stop_mode="trail"`` arms a pre-TP1 ATR trail once favorable excursion reaches
    ``early_trail_arm_atr``; ``early_stop_mode="breakeven"`` preserves the old direct
    breakeven arm. ``cost_bps`` charges round-trip trading costs at each leg-banking site.
    ``breakeven_requires_tp1`` disables that early arm until at least one target has filled;
    ``trail_after_tp1_only`` similarly prevents a pre-TP1 trail from a manual breakeven state.
    ``review_on_expiry``: when the time-stop is reached, return ``expiry_due=True`` for the
    caller to run a thesis review instead of closing here.

    ``liq_price`` is a hard backstop: sizing guarantees the stop sits inside it, so the
    stop normally fills first; liquidation only fires if the working stop is somehow looser.

    ``stop_on_close`` close-confirms the *unprotected* base stop on the intrabar (finer-tf)
    cadence: a sub-bar whose wick pierces the original stop but whose close is back inside
    the thesis is treated as noise and ridden through, so the bar-close structural/
    invalidation guard gets a say on the same cadence instead of losing to a wick. Protective
    stops (breakeven / trail, which only ever lock in gains) and liquidation still fire on the
    wick. No-op when ``stop_on_close`` is False (the single decision-bar path).
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
        total = state.realized_pnl_pct + net_of_costs(state.remaining_frac, pnl(px), cost_bps)
        new = replace(state, remaining_frac=0.0, realized_pnl_pct=total)
        return BarStep(state=new, exit_result=ExitResult(px, ts, reason, total))

    def at_cost_breakeven(px: float) -> bool:
        return abs(px - breakeven_stop(direction, entry, cost_bps)) < 1e-9

    def beyond_cost_breakeven(px: float) -> bool:
        be = breakeven_stop(direction, entry, cost_bps)
        return px > be if direction == "long" else px < be

    favorable_px = high if direction == "long" else low
    adverse_px = low if direction == "long" else high
    if state.extreme_favorable_px is None:
        extreme_favorable_px = favorable_px
    elif direction == "long":
        extreme_favorable_px = max(state.extreme_favorable_px, favorable_px)
    else:
        extreme_favorable_px = min(state.extreme_favorable_px, favorable_px)
    state = replace(
        state,
        max_favorable_pnl_pct=max(state.max_favorable_pnl_pct, pnl(favorable_px)),
        max_adverse_pnl_pct=min(state.max_adverse_pnl_pct, pnl(adverse_px)),
        extreme_favorable_px=extreme_favorable_px,
    )

    if hard_invalidation:
        return close_all(close, "invalidation")

    # Notes accumulated across the bar (stop ride-through, early arms, trail moves).
    arm_notes: list[str] = []

    if direction == "long":
        sl_hit, tp_hit = low <= working_stop, high >= target
    else:
        sl_hit, tp_hit = high >= working_stop, low <= target

    # Conservative: a bar that straddles both the stop and the target stops out first.
    if sl_hit:
        if state.breakeven and at_cost_breakeven(working_stop):
            reason = "breakeven"
        elif (state.trail_armed or pnl(working_stop) > 0) and beyond_cost_breakeven(working_stop):
            reason = "trail"
        else:
            reason = "sl"
        # Close-confirm the unprotected base stop on the intrabar cadence: a wick that pierces
        # the original stop but closes back inside the thesis is noise — ride through it and
        # let the bar-close guard decide. Protective stops still fill on the wick.
        wick_only = (close > working_stop) if direction == "long" else (close < working_stop)
        if stop_on_close and reason == "sl" and wick_only:
            arm_notes.append(
                f"stop wick ignored (close {close:g} inside stop {working_stop:g})"
            )
        else:
            return close_all(working_stop, reason)

    if liq_price is not None and _liq_hit(direction, high, low, liq_price):
        return close_all(liq_price, "liquidation")

    if tp_hit:
        if is_final_tp:
            return close_all(target, "tp")
        # Partial scale-out: bank a fraction, move stop to breakeven, advance the target.
        close_frac = state.remaining_frac * scale_out_frac
        realized = state.realized_pnl_pct + net_of_costs(close_frac, pnl(target), cost_bps)
        new = replace(
            state,
            remaining_frac=state.remaining_frac - close_frac,
            realized_pnl_pct=realized,
            working_stop=breakeven_stop(direction, entry, cost_bps),  # breakeven, net of costs
            tp_index=tp_idx + 1,
            breakeven=True,
        )
        return BarStep(
            state=new,
            partial=PartialFill(target, ts, close_frac, pnl(target), tp_idx),
            notes=[f"scaled out {close_frac:.2f} at tp{tp_idx + 1}; stop→breakeven"],
        )

    # Early protection before TP1. In trail mode this arms an ATR trail without jumping
    # straight to breakeven; breakeven mode preserves the legacy behavior for comparison.
    if atr and state.tp_index == 0:
        favorable = (high - entry) if direction == "long" else (entry - low)
        if (
            early_stop_mode == "trail"
            and not state.trail_armed
            and early_trail_arm_atr
            and favorable >= early_trail_arm_atr * atr
        ):
            state = replace(state, trail_armed=True)
            arm_notes.append(f"early_trail armed at {early_trail_arm_atr:g}x ATR")
        can_arm_breakeven = not (breakeven_requires_tp1 and state.tp_index == 0)
        if (
            early_stop_mode == "breakeven"
            and not state.breakeven
            and can_arm_breakeven
            and breakeven_arm_atr
        ):
            profit_floor = entry * (2.0 * cost_bps / 10_000.0) * breakeven_arm_cost_mult
            if favorable >= breakeven_arm_atr * atr and favorable >= profit_floor:
                locked = profit_lock_stop(
                    direction, entry, cost_bps, breakeven_lock_atr * atr
                )
                state = replace(state, working_stop=locked, breakeven=True)
                if breakeven_lock_atr:
                    arm_notes.append(
                        f"armed profit-lock early at {breakeven_arm_atr:g}x ATR "
                        f"(+{breakeven_lock_atr:g}x ATR locked)"
                    )
                else:
                    arm_notes.append(f"armed breakeven early at {breakeven_arm_atr:g}x ATR")

    if state.trail_armed and state.tp_index == 0 and early_trail_atr_mult and atr:
        cur_ws = state.working_stop if state.working_stop is not None else full_stop
        anchor = _trail_anchor(trail_mode, direction, close, state.extreme_favorable_px)
        if direction == "long":
            ws = max(cur_ws, anchor - early_trail_atr_mult * atr)
        else:
            ws = min(cur_ws, anchor + early_trail_atr_mult * atr)
        if ws != cur_ws or state.working_stop is None:
            state = replace(state, working_stop=ws)
            arm_notes.append(
                f"early_trail stop -> {ws:g} ({early_trail_atr_mult:g}x ATR, {trail_mode})"
            )

    # Conditional loser-cut: give losers the protective ratchet winners already get. Once a
    # still-red, never-armed trade has spent ``loss_cut_hold_frac`` of its hold window (the caller
    # passes the resulting ``loss_cut_at`` timestamp), tighten its stop to ``loss_cut_atr_mult`` ATR
    # from entry — only-tighten, so it can't widen risk, and it takes effect on the next bar's stop
    # check exactly like the trail below. Skips protected/profitable trades (those already ride the
    # winner-side machine). Fully causal (no future data); inert when the knobs are 0.
    if (
        loss_cut_at is not None
        and ts >= loss_cut_at
        and loss_cut_atr_mult
        and atr
        and not state.breakeven
        and not state.trail_armed
        and pnl(close) <= 0
    ):
        cur_ws = state.working_stop if state.working_stop is not None else full_stop
        if direction == "long":
            ws = max(cur_ws, entry - loss_cut_atr_mult * atr)
        else:
            ws = min(cur_ws, entry + loss_cut_atr_mult * atr)
        if ws != cur_ws:
            state = replace(state, working_stop=ws)
            arm_notes.append(f"loss-cut stop -> {ws:g} ({loss_cut_atr_mult:g}x ATR)")

    # Time-stop: let breakeven-protected runners keep going, and never cut a trade that is
    # currently in profit; close only what is still flat/at risk and unprotected. When
    # review_on_expiry is set, hand the decision to the caller (thesis review) instead.
    # expires_at is None when the time-stop is disabled (settings.exits.max_hold_bars <= 0).
    if expires_at is not None and ts >= expires_at and not state.breakeven and pnl(close) <= 0:
        if review_on_expiry:
            return BarStep(state=state, expiry_due=True, notes=arm_notes)
        return close_all(close, "expiry")

    # Trail the runner once it is protected. Base off the CURRENT working stop, which may
    # have just been set to entry by the early arm above.
    new_state = state
    can_trail = not (trail_after_tp1_only and state.tp_index == 0)
    if state.breakeven and can_trail and trail_atr_mult and atr:
        cur_ws = state.working_stop if state.working_stop is not None else full_stop
        anchor = _trail_anchor(trail_mode, direction, close, state.extreme_favorable_px)
        if direction == "long":
            ws = max(cur_ws, anchor - trail_atr_mult * atr)
        else:
            ws = min(cur_ws, anchor + trail_atr_mult * atr)
        if ws != cur_ws:
            arm_notes.append(f"trail stop -> {ws:g} ({trail_atr_mult:g}x ATR, {trail_mode})")
        new_state = replace(state, working_stop=ws)
    return BarStep(state=new_state, notes=arm_notes)
