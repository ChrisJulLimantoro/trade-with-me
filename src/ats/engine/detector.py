"""Detector — the glue between the rule engine and the trade lifecycle.

For a given "now" (a feature row), against the active plan it:
1. evaluates invalidation rules → may kill the plan and close open trades,
2. for each armed setup, runs the rule engine; on detection asks confirm_setup,
   applies risk, and opens a paper trade.

It also reconciles open trades against the current bar (SL / TP / expiry). LLM calls
(confirm_setup) only happen on a detection, keeping cost proportional to opportunities.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ats.config import settings
from ats.db.models import LlmCall, PaperTrade, Plan, Setup
from ats.engine import state
from ats.engine.invalidation import evaluate_invalidation
from ats.engine.rule_engine import evaluate_setup
from ats.execution.executor import (
    close_paper_trade,
    open_paper_trade,
    record_partial_exit,
    update_trade_state,
)
from ats.execution.reconcile import (
    ExitResult,
    PartialFill,
    TradeState,
    _pnl_pct,
    net_of_costs,
    step_trade,
)
from ats.engine.timeframes import timeframe_to_timedelta
from ats import trace
from ats.llm.client import LlmClient
from ats.logging import get_logger
from ats.risk.manager import assess, regime_allows

log = get_logger(__name__)


@dataclass
class ClosedTradeInfo:
    """Minimal closed-trade data emitted by _close_trade for metrics collection."""

    direction: str
    entry_price: float
    stop_loss: float
    exit_price: float
    exit_reason: str
    margin_usd: float
    notional_usd: float
    leverage: float | None
    pnl_pct: float
    pnl_usd: float
    atr_at_close: float | None  # atr_14 from the bar that closed the trade


@dataclass
class TickReport:
    now: datetime | None = None
    plan_id: uuid.UUID | None = None
    detections: int = 0
    opened: int = 0
    closed: int = 0
    confirm_calls: int = 0
    observe_calls: int = 0
    risk_rejected: int = 0
    plan_invalidated: bool = False
    paused: bool = False
    closed_trades: list[ClosedTradeInfo] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _f(v: Any) -> float:
    return float(v) if isinstance(v, Decimal) else float(v)


def clamp_tightened_stop(direction: str, cur_stop: float, new_stop: float, price: float) -> float:
    """Clamp an observation's proposed stop so it can only tighten, never widen risk.

    The stop may move toward price but never away from it and never past the current price
    (which would be an instant, pathological fill). Returns the clamped working stop.
    """
    if direction == "long":
        return min(max(cur_stop, new_stop), price)
    return max(min(cur_stop, new_stop), price)


def clamp_extended_tp(direction: str, cur_final_tp: float, new_tp: float) -> float:
    """Clamp a proposed take-profit so the final target can only extend outward."""
    return max(cur_final_tp, new_tp) if direction == "long" else min(cur_final_tp, new_tp)


def _setup_dict(s: Setup) -> dict[str, Any]:
    return {
        "setup_id": s.setup_id,
        "plan_id": s.plan_id,
        "symbol": s.symbol,
        "direction": s.direction,
        "status": s.status,
        "entry_zone_low": _f(s.entry_zone_low),
        "entry_zone_high": _f(s.entry_zone_high),
        "stop_loss": _f(s.stop_loss),
        "take_profit": [float(x) for x in s.take_profit],
        "size_pct": _f(s.size_pct),
        "hard_rules": s.hard_rules,
        "soft_rules": s.soft_rules,
        "invalidation_rules": s.invalidation_rules,
        "expires_at": s.expires_at,
    }


async def _open_trades_for(session: AsyncSession, symbol: str) -> list[PaperTrade]:
    stmt = select(PaperTrade).where(PaperTrade.symbol == symbol, PaperTrade.status == "open")
    return list((await session.execute(stmt)).scalars().all())


async def _close_trade(
    session: AsyncSession,
    client: LlmClient | None,
    trade: PaperTrade,
    exit_result: ExitResult,
    report: TickReport,
    *,
    atr_at_close: float | None = None,
) -> None:
    """Single close path: record the close, then run the episodic post-mortem.

    Centralised so every exit route (stop/target/expiry, hard invalidation, observe-exit)
    reflects exactly once. Reflection failures never break the trade close.
    """
    closed_result = await close_paper_trade(
        session,
        trade.trade_id,
        exit_result,
        equity_usd=settings.paper_equity_usd,
        size_pct=_f(trade.size_pct),
    )
    if closed_result is None:
        return
    notional_usd = (
        _f(trade.notional_usd)
        if trade.notional_usd is not None
        else settings.paper_equity_usd * _f(trade.size_pct)
    )
    if trade.margin_usd is not None:
        margin_usd = _f(trade.margin_usd)
    elif trade.leverage is not None and _f(trade.leverage) > 0:
        margin_usd = notional_usd / _f(trade.leverage)
    else:
        margin_usd = notional_usd
    report.closed += 1
    report.closed_trades.append(
        ClosedTradeInfo(
            direction=trade.direction,
            entry_price=_f(trade.entry_price),
            stop_loss=_f(trade.stop_loss),
            exit_price=closed_result.exit_price,
            exit_reason=closed_result.exit_reason,
            margin_usd=margin_usd,
            notional_usd=notional_usd,
            leverage=_f(trade.leverage) if trade.leverage is not None else None,
            pnl_pct=closed_result.pnl_pct,
            pnl_usd=round(notional_usd * exit_result.pnl_pct, 2),
            atr_at_close=atr_at_close,
        )
    )
    if client is not None and settings.memory_enabled:
        try:
            from ats.learning.post_mortem import reflect_and_store

            await reflect_and_store(session, client, trade, closed_result)
        except Exception as exc:  # noqa: BLE001 — reflection must never break the close
            log.warning("reflect_failed", trade_id=str(trade.trade_id), error=str(exc))


async def _advance_trade(
    session: AsyncSession,
    client: LlmClient | None,
    trade: PaperTrade,
    candles: list[dict[str, Any]],
    report: TickReport,
    *,
    atr: float | None,
    feature_row: dict[str, Any] | None,
) -> None:
    """Walk one open trade across a sequence of candles (finer-tf bars or a single bar).

    Each candle: step the deterministic exit machine (stop / target / scale-out / trail /
    expiry). Every ``observe_every_bars`` candles, consult the observation agent and apply
    its (code-clamped) adjustment. Stops on the first full close.
    """
    if settings.max_hold_bars <= 0:
        expires_at: datetime | None = None  # time-stop disabled — trades run to SL/TP
    else:
        raw_expiry = (trade.trade_metadata or {}).get("expires_at")
        last_ts = candles[-1]["open_time"] if candles else trade.entry_time
        expires_at = (
            datetime.fromisoformat(raw_expiry) if isinstance(raw_expiry, str) else last_ts
        )
    trade_d = {
        "direction": trade.direction,
        "entry_price": _f(trade.entry_price),
        "stop_loss": _f(trade.stop_loss),
        "take_profit": [float(x) for x in trade.take_profit],
    }
    state = TradeState.from_metadata(trade.trade_metadata)
    liq_price = _f(trade.liq_price) if trade.liq_price is not None else None
    cost_bps = settings.fee_bps + settings.slippage_bps
    # Review-gate the time-stop only when the exit manager is actually available.
    can_review = (
        settings.expiry_review_enabled
        and client is not None
        and settings.observe_enabled
        and feature_row is not None
    )
    md0 = trade.trade_metadata or {}
    extensions = int(md0.get("hold_extensions", 0))
    window_s = float(md0.get("hold_window_seconds", 0.0))
    bars = 0
    for candle in candles:
        if trade.entry_time >= candle["open_time"]:
            continue  # entered this bar or later — can't exit on its own entry bar
        step = step_trade(
            trade_d,
            candle,
            state,
            expires_at=expires_at,
            scale_out_frac=settings.scale_out_frac,
            trail_atr_mult=settings.trail_atr_mult,
            atr=atr,
            breakeven_arm_atr=settings.breakeven_arm_atr,
            breakeven_arm_cost_mult=settings.breakeven_arm_cost_mult,
            cost_bps=cost_bps,
            review_on_expiry=can_review,
            liq_price=liq_price,
        )
        if step.closed:
            await _close_trade(session, client, trade, step.exit_result, report, atr_at_close=atr)
            return
        if step.expiry_due:
            # Time-stop reached: review the thesis instead of a blind close. A clear reversal
            # closes via EXIT_NOW; otherwise grant one more full hold window, up to a backstop.
            if await _observe_and_adjust(
                session, client, trade, trade_d, candle, state, report, feature_row=feature_row
            ):
                return  # exit manager closed it (EXIT_NOW)
            if extensions < settings.max_hold_extensions and window_s > 0:
                extensions += 1
                expires_at = candle["open_time"] + timedelta(seconds=window_s)
                new_md = dict(trade.trade_metadata or {})
                new_md.update(expires_at=expires_at.isoformat(), hold_extensions=extensions)
                trade.trade_metadata = new_md
                await session.flush()
                report.notes.append(
                    f"hold extended {extensions}/{settings.max_hold_extensions} (thesis intact)"
                )
                bars += 1
                continue
            # Backstop: extensions exhausted → honour the time-stop close (net of costs).
            close_px = float(candle["close"])
            total = state.realized_pnl_pct + net_of_costs(
                state.remaining_frac,
                _pnl_pct(trade.direction, _f(trade.entry_price), close_px),
                cost_bps,
            )
            await _close_trade(
                session, client, trade,
                ExitResult(close_px, candle["open_time"], "expiry", total),
                report, atr_at_close=atr,
            )
            return
        if step.partial is not None:
            await record_partial_exit(
                session, trade.trade_id, step.partial, step.state,
                equity_usd=settings.paper_equity_usd, size_pct=_f(trade.size_pct),
            )
        elif step.state.working_stop != state.working_stop:
            await update_trade_state(session, trade.trade_id, step.state)
        state = step.state
        bars += 1

        if (
            client is not None
            and settings.observe_enabled
            and feature_row is not None
            and len(candles) > 1  # only on the finer-tf path
            and bars % settings.observe_every_bars == 0
        ):
            closed = await _observe_and_adjust(
                session, client, trade, trade_d, candle, state, report, feature_row=feature_row
            )
            if closed:
                return


async def _reconcile_open_trades(
    session: AsyncSession,
    symbol: str,
    candle: dict[str, Any],
    report: TickReport,
    *,
    atr: float | None = None,
    client: LlmClient | None = None,
    fine_candles: list[dict[str, Any]] | None = None,
    feature_row: dict[str, Any] | None = None,
) -> None:
    """Advance each open trade. Uses ``fine_candles`` (finer-tf) when given, else this bar."""
    candles = fine_candles if fine_candles else [candle]
    for trade in await _open_trades_for(session, symbol):
        await _advance_trade(
            session, client, trade, candles, report, atr=atr, feature_row=feature_row
        )


async def _observe_and_adjust(
    session: AsyncSession,
    client: LlmClient,
    trade: PaperTrade,
    trade_d: dict[str, Any],
    candle: dict[str, Any],
    state: TradeState,
    report: TickReport,
    *,
    feature_row: dict[str, Any],
) -> bool:
    """Consult the observation agent for an open trade; apply a code-clamped adjustment.

    Returns True if the trade was fully closed (EXIT_NOW). All adjustments are clamped so the
    LLM can only protect/extend, never widen risk: a tightened stop moves toward price (never
    away, never past it); a raised target extends outward only.
    """
    direction = trade.direction
    entry = trade_d["entry_price"]
    close = float(candle["close"])
    ts = candle["open_time"]
    cur_ws = state.working_stop if state.working_stop is not None else trade_d["stop_loss"]

    def leg_pnl(px: float) -> float:
        return _pnl_pct(direction, entry, px)

    unrealized = state.realized_pnl_pct + state.remaining_frac * leg_pnl(close)
    env = {
        "now": ts,
        "symbol": trade.symbol,
        "direction": direction,
        "entry_price": entry,
        "entry_time": trade.entry_time,
        "current_price": close,
        "unrealized_pct": round(unrealized, 6),
        "working_stop": cur_ws,
        "take_profit": list(trade_d["take_profit"]),
        "remaining_frac": state.remaining_frac,
        "breakeven": state.breakeven,
        "observe_timeframe": settings.observe_timeframe,
        "features_now": feature_row,
    }
    obs, llm = await client.observe_trade(env, symbol=trade.symbol)
    report.observe_calls += 1
    session.add(
        LlmCall(
            call_id=uuid.uuid4(),
            kind="observe_trade",
            model=llm.model,
            mock=llm.mock,
            symbol=trade.symbol,
            plan_id=trade.plan_id,
            setup_id=trade.setup_id,
            input_tokens=llm.input_tokens,
            output_tokens=llm.output_tokens,
            cost_usd=llm.cost_usd,
            latency_ms=llm.latency_ms,
            parse_ok=llm.parse_ok,
            response=llm.raw,
        )
    )
    if not llm.parse_ok or obs is None or obs.action == "HOLD":
        return False

    if obs.action == "TIGHTEN_STOP" and obs.new_stop is not None:
        # Move the stop toward price only; never loosen it, never put it past the market.
        new_ws = clamp_tightened_stop(direction, cur_ws, float(obs.new_stop), close)
        if new_ws != cur_ws:
            state.working_stop = new_ws
            await update_trade_state(session, trade.trade_id, state)
            trace.outcome(f"OBSERVE tighten stop -> {new_ws:g} ({obs.reason})")
        return False

    if obs.action == "RAISE_TP" and obs.new_tp:
        tps = list(trade_d["take_profit"])
        final = len(tps) - 1
        # Extend the final target outward only — let a clear winner run, never cut it short.
        tps[final] = clamp_extended_tp(direction, tps[final], float(obs.new_tp[-1]))
        if tps != trade_d["take_profit"]:
            trade_d["take_profit"] = tps
            trade.take_profit = tps
            await session.flush()
            trace.outcome(f"OBSERVE raise tp -> {tps[final]:g} ({obs.reason})")
        return False

    cost_bps = settings.fee_bps + settings.slippage_bps
    if obs.action == "SCALE_OUT" and obs.scale_frac and state.remaining_frac > 0:
        frac = min(max(float(obs.scale_frac), 0.0), 1.0) * state.remaining_frac
        if frac > 0:
            leg = leg_pnl(close)
            state.realized_pnl_pct += net_of_costs(frac, leg, cost_bps)
            state.remaining_frac -= frac
            state.working_stop = entry  # protect the runner at breakeven
            state.breakeven = True
            await record_partial_exit(
                session, trade.trade_id,
                PartialFill(close, ts, frac, leg, state.tp_index), state,
                equity_usd=settings.paper_equity_usd, size_pct=_f(trade.size_pct),
            )
            trace.outcome(f"OBSERVE scale out {frac:.2f} @ {close:g} ({obs.reason})")
        return False

    if obs.action == "EXIT_NOW":
        conf = float(obs.confidence) if obs.confidence is not None else 0.0
        if conf >= settings.observe_exit_min_conf:
            total = state.realized_pnl_pct + net_of_costs(
                state.remaining_frac, leg_pnl(close), cost_bps
            )
            raw_atr = feature_row.get("atr_14")
            await _close_trade(
                session, client, trade,
                ExitResult(close, ts, "observe_exit", total), report,
                atr_at_close=float(raw_atr) if raw_atr is not None else None,
            )
            trace.outcome(f"OBSERVE exit now @ {close:g} (conf {conf:.2f}; {obs.reason})")
            return True
        trace.outcome(
            f"OBSERVE exit ignored (conf {conf:.2f} < {settings.observe_exit_min_conf})"
        )
    return False


async def _handle_invalidation(
    session: AsyncSession,
    client: LlmClient | None,
    plan: Plan,
    setups: list[Setup],
    feature_row: dict[str, Any],
    prev_row: dict[str, Any] | None,
    candle_closed: bool,
    report: TickReport,
) -> bool:
    """Evaluate invalidation across the plan's setups. Returns True if plan was killed."""
    paused = False
    hard = False
    for s in setups:
        sev = evaluate_invalidation(
            s.invalidation_rules, feature_row, prev_row, candle_closed=candle_closed
        )
        if sev == "hard":
            hard = True
            s.status = "invalidated"
            # close any open trade for this setup at the current close, banking partials
            for trade in await _open_trades_for(session, s.symbol):
                if trade.setup_id == s.setup_id:
                    price = feature_row["price"]
                    direction = trade.direction
                    entry = _f(trade.entry_price)
                    raw = (price - entry) / entry if entry else 0.0
                    leg = raw if direction == "long" else -raw
                    st = TradeState.from_metadata(trade.trade_metadata)
                    cost_bps = settings.fee_bps + settings.slippage_bps
                    total = st.realized_pnl_pct + net_of_costs(
                        st.remaining_frac, leg, cost_bps
                    )
                    raw_atr = feature_row.get("atr_14")
                    await _close_trade(
                        session,
                        client,
                        trade,
                        ExitResult(price, feature_row["open_time"], "invalidation", total),
                        report,
                        atr_at_close=float(raw_atr) if raw_atr is not None else None,
                    )
        elif sev == "soft":
            paused = True
        elif sev == "warning":
            # Thesis weakening: protect the open trade by moving its stop to breakeven
            # and pause new entries this bar.
            paused = True
            for trade in await _open_trades_for(session, s.symbol):
                if trade.setup_id != s.setup_id or trade.status != "open":
                    continue
                st = TradeState.from_metadata(trade.trade_metadata)
                if not st.breakeven:
                    st.working_stop = _f(trade.entry_price)
                    st.breakeven = True
                    await update_trade_state(session, trade.trade_id, st)
                    report.notes.append(
                        f"warning invalidation on setup {s.setup_id}: stop→breakeven"
                    )

    if hard:
        plan.status = "invalidated"
        report.plan_invalidated = True
        await session.flush()
        return True
    if paused:
        report.notes.append("soft invalidation: new entries paused")
        report.paused = True
    return False


async def _confirm_and_execute(
    session: AsyncSession,
    client: LlmClient,
    plan: Plan,
    setup: Setup,
    setup_d: dict[str, Any],
    ev: Any,
    feature_row: dict[str, Any],
    now: datetime,
    open_positions: list[dict[str, Any]],
    report: TickReport,
    *,
    tf: str,
) -> None:
    from ats.llm.schemas import ConfirmOutput

    confirm_env = {
        "now": now,
        "symbol": setup.symbol,
        "setup": {k: v for k, v in setup_d.items() if k not in ("expires_at",)},
        "rule_eval": {
            "price": ev.price,
            "hard_ok": ev.hard_ok,
            "soft_score": ev.soft_score,
            "failed_hard": ev.failed_hard,
        },
        "features_now": feature_row,
        "plan_bias": plan.market_bias,
    }
    confirm, llm = await client.confirm_setup(confirm_env, symbol=setup.symbol)
    report.confirm_calls += 1

    # Deterministic fallback: if the LLM parse failed but the deterministic engine produced a
    # strong signal, treat it as a REDUCE_SIZE confirm (0.75x) rather than silently dropping
    # a valid opportunity. This catches JSON formatting hiccups on otherwise good setups.
    if not llm.parse_ok or confirm is None:
        if ev.hard_ok and ev.soft_score >= settings.soft_threshold:
            log.info(
                "confirm_parse_fail_fallback",
                setup_id=str(setup.setup_id),
                soft_score=ev.soft_score,
            )
            confirm = ConfirmOutput(
                action="REDUCE_SIZE",
                reason="parse_fail_fallback: deterministic signal strong; executing at reduced size",
                size_multiplier=0.75,
            )
        else:
            trace.confirm(
                now=now,
                plan_id=plan.plan_id,
                setup_id=setup.setup_id,
                ev=ev,
                action="PARSE_FAIL",
                reason=None,
                size_multiplier=1.0,
            )
            session.add(
                LlmCall(
                    call_id=uuid.uuid4(),
                    kind="confirm_setup",
                    model=llm.model,
                    mock=llm.mock,
                    symbol=setup.symbol,
                    plan_id=plan.plan_id,
                    setup_id=setup.setup_id,
                    input_tokens=llm.input_tokens,
                    output_tokens=llm.output_tokens,
                    cost_usd=llm.cost_usd,
                    latency_ms=llm.latency_ms,
                    parse_ok=llm.parse_ok,
                    response=llm.raw,
                )
            )
            trace.outcome("SKIPPED: parse_fail (weak signal — no fallback)")
            report.notes.append(f"setup {setup.setup_id} not executed (parse_fail)")
            return

    trace.confirm(
        now=now,
        plan_id=plan.plan_id,
        setup_id=setup.setup_id,
        ev=ev,
        action=confirm.action,
        reason=confirm.reason,
        size_multiplier=confirm.size_multiplier,
    )
    session.add(
        LlmCall(
            call_id=uuid.uuid4(),
            kind="confirm_setup",
            model=llm.model,
            mock=llm.mock,
            symbol=setup.symbol,
            plan_id=plan.plan_id,
            setup_id=setup.setup_id,
            input_tokens=llm.input_tokens,
            output_tokens=llm.output_tokens,
            cost_usd=llm.cost_usd,
            latency_ms=llm.latency_ms,
            parse_ok=llm.parse_ok,
            response=llm.raw,
        )
    )

    if confirm.action in ("WAIT", "REJECT"):
        if confirm.action == "REJECT":
            setup.status = "rejected"
        trace.outcome(f"SKIPPED: {confirm.action}")
        report.notes.append(f"setup {setup.setup_id} not executed ({confirm.action})")
        return

    multiplier = confirm.size_multiplier if confirm.action == "REDUCE_SIZE" else 1.0
    raw_atr = feature_row.get("atr_14")
    decision = assess(
        setup_d,
        price=ev.price,
        open_positions=open_positions,
        equity_usd=settings.paper_equity_usd,
        risk_per_trade_pct=settings.risk_per_trade_pct,
        max_leverage=settings.max_leverage,
        min_rr=settings.min_rr,
        max_margin_pct_per_trade=settings.max_margin_pct_per_trade,
        max_total_margin_pct=settings.max_total_margin_pct,
        max_portfolio_risk_pct=settings.max_portfolio_risk_pct,
        size_multiplier=multiplier,
        atr=float(raw_atr) if raw_atr is not None else None,
        min_stop_atr_mult=settings.min_stop_atr_mult,
    )
    if not decision.approved:
        report.risk_rejected += 1
        trace.outcome(f"SKIPPED: risk-rejected — {'; '.join(decision.reasons)}")
        report.notes.append(f"setup {setup.setup_id} risk-rejected: {decision.reasons}")
        return

    trade_id = await open_paper_trade(
        session,
        {**setup_d, "trade_metadata": None},
        entry_price=ev.price,
        entry_time=now,
        size_pct=decision.size_pct,
        reasons=[f"confirm:{confirm.action}", *decision.reasons],
        leverage=decision.leverage,
        margin_usd=decision.margin_usd,
        notional_usd=decision.notional_usd,
        liq_price=decision.liq_price,
        risk_usd=decision.risk_usd,
    )
    # Time-stop the OPEN trade from its own entry bar, not the setup's entry-window
    # expiry: a trade that triggers late in a plan's life still gets a full hold budget,
    # so a healthy, still-running position is no longer cut short by the plan clock.
    trade = await session.get(PaperTrade, trade_id)
    if trade is not None and settings.max_hold_bars > 0:
        hold = timeframe_to_timedelta(tf) * settings.max_hold_bars
        trade.trade_metadata = {
            "expires_at": (now + hold).isoformat(),
            # Window length each thesis-review extension grants (see _advance_trade).
            "hold_window_seconds": hold.total_seconds(),
        }
    setup.status = "realized"
    setup.detected_at = now
    open_positions.append(
        {
            "symbol": setup.symbol,
            "margin_usd": decision.margin_usd,
            "notional_usd": decision.notional_usd,
            "risk_usd": decision.risk_usd,
            "leverage": decision.leverage,
            "size_pct": decision.size_pct,
        }
    )
    report.opened += 1
    trace.outcome(f"EXECUTED — {'; '.join(decision.reasons)}")
    await session.flush()


async def evaluate_now(
    session: AsyncSession,
    client: LlmClient,
    *,
    symbol: str,
    tf: str,
    feature_row: dict[str, Any],
    prev_row: dict[str, Any] | None,
    candle_closed: bool = True,
    now: datetime | None = None,
    fine_candles: list[dict[str, Any]] | None = None,
) -> TickReport:
    """Run one full evaluation of the active plan against the current feature row.

    When ``fine_candles`` is supplied (a position is open and a finer timeframe is
    configured), open trades are reconciled bar-by-bar over those candles with the
    observation agent in the loop; otherwise the single decision-bar candle is used.
    """
    now = now or feature_row["open_time"]
    report = TickReport(now=now)

    candle = {
        "open_time": feature_row["open_time"],
        "high": feature_row["high"],
        "low": feature_row["low"],
        "close": feature_row["close"],
    }
    atr = feature_row.get("atr_14")
    await _reconcile_open_trades(
        session, symbol, candle, report,
        atr=_f(atr) if atr is not None else None,
        client=client, fine_candles=fine_candles, feature_row=feature_row,
    )

    plan = await state.active_plan(session, symbol)
    if plan is None:
        report.notes.append("no active plan")
        return report
    report.plan_id = plan.plan_id

    if now >= plan.expires_at:
        plan.status = "expired"
        await session.flush()
        report.notes.append("plan expired")
        return report

    setups = await state.plan_setups(session, plan.plan_id)
    killed = await _handle_invalidation(
        session, client, plan, setups, feature_row, prev_row, candle_closed, report
    )
    if killed or report.paused:
        return report

    open_positions = await state.open_positions(session, symbol=symbol)
    if open_positions:
        # One position per symbol: no new entry is possible, so don't spend an LLM
        # confirm call this bar. Existing trades are still reconciled above.
        report.notes.append("position open: skipping new entries")
        return report
    for setup in setups:
        if setup.status != "active":
            continue
        if now >= setup.expires_at:
            setup.status = "expired"
            continue
        setup_d = _setup_dict(setup)
        ev = evaluate_setup(setup_d, feature_row, prev_row, soft_threshold=settings.soft_threshold)
        if not ev.detected:
            continue
        report.detections += 1
        if settings.regime_filter and not regime_allows(plan.regime_cell, setup.direction):
            trace.outcome(f"SKIPPED: regime {plan.regime_cell} disallows {setup.direction}")
            report.notes.append(
                f"setup {setup.setup_id} regime-filtered ({plan.regime_cell}/{setup.direction})"
            )
            continue
        await _confirm_and_execute(
            session, client, plan, setup, setup_d, ev, feature_row, now, open_positions, report,
            tf=tf,
        )

    await session.flush()
    return report
