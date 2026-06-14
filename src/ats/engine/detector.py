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

from ats import trace
from ats.config import settings
from ats.db.models import LlmCall, PaperTrade, Plan, Setup
from ats.engine import state
from ats.engine.invalidation import evaluate_invalidation
from ats.engine.rule_engine import entry_confirmed, evaluate_setup
from ats.engine.timeframes import timeframe_to_timedelta
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
    breakeven_stop,
    net_of_costs,
    step_trade,
)
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


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _round_or_none(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(value, digits)


def _trade_notional_margin(trade: PaperTrade) -> tuple[float, float]:
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
    return notional_usd, margin_usd


def _margin_pnl_pct(trade: PaperTrade, notional_pnl_pct: float) -> float:
    notional_usd, margin_usd = _trade_notional_margin(trade)
    return (notional_usd * notional_pnl_pct) / margin_usd if margin_usd > 0 else 0.0


def _hold_context(trade: PaperTrade, now: datetime) -> dict[str, Any]:
    md = trade.trade_metadata or {}
    entry_time = _parse_dt(trade.entry_time) or now
    expires_at = _parse_dt(md.get("expires_at"))
    window_s_raw = md.get("hold_window_seconds")
    window_s = float(window_s_raw) if window_s_raw else None
    held_s = max(0.0, (now - entry_time).total_seconds())
    remaining_s = None if expires_at is None else (expires_at - now).total_seconds()
    current_window_progress = None
    if window_s and remaining_s is not None:
        current_window_progress = max(0.0, (window_s - max(0.0, remaining_s)) / window_s)
    held_bars_estimate = (
        held_s / window_s * settings.max_hold_bars
        if window_s and settings.max_hold_bars > 0
        else None
    )
    return {
        "held_seconds": round(held_s, 3),
        "held_minutes": round(held_s / 60.0, 3),
        "held_bars_estimate": _round_or_none(held_bars_estimate, 3),
        "hold_window_seconds": window_s,
        "hold_progress": _round_or_none(held_s / window_s if window_s else None),
        "current_window_progress": _round_or_none(current_window_progress),
        "expires_at": expires_at,
        "seconds_to_time_stop": _round_or_none(remaining_s, 3),
        "hold_extensions_used": int(md.get("hold_extensions", 0)),
        "max_hold_extensions": settings.max_hold_extensions,
    }


def _directional_feature_state(direction: str, value: Any) -> str:
    if value is None:
        return "unknown"
    raw = float(value)
    with_trade = raw if direction == "long" else -raw
    if with_trade > 0:
        return "with"
    if with_trade < 0:
        return "against"
    return "flat"


def _entry_zone_location(entry: float, zone_low: float | None, zone_high: float | None) -> str:
    if zone_low is None or zone_high is None or zone_high <= zone_low:
        return "unknown"
    pos = (entry - zone_low) / (zone_high - zone_low)
    if pos <= 0.33:
        return "lower_zone"
    if pos >= 0.67:
        return "upper_zone"
    return "mid_zone"


def _atr_distance(a: float | None, b: float | None, atr: float | None) -> float | None:
    if a is None or b is None or atr is None or atr <= 0:
        return None
    return round(abs(a - b) / atr, 4)


def _observer_thesis_health(
    *,
    current_r: float | None,
    max_favorable_r: float | None,
    max_adverse_r: float | None,
    momentum_state: str,
    cvd_state: str,
    volume_followthrough: str,
    squeeze_risk: str,
) -> tuple[str, list[str], str]:
    reasons: list[str] = []

    if current_r is not None and current_r <= -0.5:
        reasons.append("current_loss_beyond_half_r")
    if max_favorable_r is not None and max_favorable_r < 0.25:
        reasons.append("poor_favorable_excursion")
    if max_adverse_r is not None and max_adverse_r <= -0.75:
        reasons.append("large_adverse_excursion")
    if momentum_state == "against":
        reasons.append("momentum_against_trade")
    if cvd_state == "against":
        reasons.append("cvd_against_trade")
    if volume_followthrough == "weak":
        reasons.append("weak_volume_followthrough")
    if squeeze_risk == "against_trade":
        reasons.append("squeeze_risk_against_trade")

    broken = (
        current_r is not None
        and current_r <= -0.5
        and max_favorable_r is not None
        and max_favorable_r < 0.25
        and (momentum_state == "against" or squeeze_risk == "against_trade")
    )
    decaying = len(reasons) >= 2 or (
        max_adverse_r is not None
        and max_favorable_r is not None
        and abs(max_adverse_r) > max(0.25, max_favorable_r * 2)
    )
    if broken:
        return "broken", reasons, "exit"
    if decaying:
        if current_r is not None and current_r > 0:
            return "decaying", reasons, "tighten_or_scale"
        return "decaying", reasons, "cut_or_tighten"
    if current_r is not None and current_r > 1.0 and momentum_state == "with":
        return "healthy", reasons, "let_winner_work"
    return "healthy", reasons, "hold"


def _observer_call_due(status: str | None, *, stale_candidate: bool, bars: int) -> bool:
    """Event-driven observer gate (#5): is an LLM observation worth spending this bar?

    Consult the observer when the deterministic thesis is decaying/broken, the trade is a
    stale candidate, or the periodic health fallback cadence has elapsed. A healthy, still-
    traveling trade is left to deterministic management (step_trade) and skips the LLM call.
    """
    if status in {"decaying", "broken"} or stale_candidate:
        return True
    fb = settings.observe_health_fallback_bars
    return fb > 0 and bars % fb == 0


def _observer_context(
    *,
    trade: PaperTrade,
    trade_d: dict[str, Any],
    feature_row: dict[str, Any],
    hold: dict[str, Any],
    current_leg_notional: float,
    max_favorable_notional: float,
    max_adverse_notional: float,
) -> dict[str, Any]:
    direction = trade.direction
    entry = float(trade_d["entry_price"])
    stop = float(trade_d["stop_loss"])
    md = trade.trade_metadata or {}
    atr = feature_row.get("atr_14")
    atr_f = float(atr) if atr is not None else None
    zone_low_raw = md.get("entry_zone_low")
    zone_high_raw = md.get("entry_zone_high")
    zone_low = float(zone_low_raw) if zone_low_raw is not None else None
    zone_high = float(zone_high_raw) if zone_high_raw is not None else None
    risk_per_unit = abs(_pnl_pct(direction, entry, stop))

    def to_r(value: float) -> float | None:
        if risk_per_unit <= 0:
            return None
        return round(value / risk_per_unit, 4)

    current_r = to_r(current_leg_notional)
    max_favorable_r = to_r(max_favorable_notional)
    max_adverse_r = to_r(max_adverse_notional)
    bars_est = hold.get("held_bars_estimate")
    bars_since_entry = int(round(float(bars_est))) if bars_est is not None else None
    bars_without_positive_mfe = (
        bars_since_entry
        if bars_since_entry is not None and (max_favorable_r is None or max_favorable_r < 0.1)
        else 0
    )

    momentum_state = _directional_feature_state(direction, feature_row.get("macd_hist"))
    cvd_state = _directional_feature_state(direction, feature_row.get("cvd_slope_10"))
    vol_z = feature_row.get("vol_zscore_20")
    volume_followthrough = "unknown"
    if vol_z is not None:
        volume_followthrough = "strong" if float(vol_z) >= 0 else "weak"

    rsi = feature_row.get("rsi_14")
    ema_20 = feature_row.get("ema_20")
    ema_50 = feature_row.get("ema_50")
    price = feature_row.get("close") or feature_row.get("price")
    squeeze_risk = "neutral"
    if rsi is not None and price is not None:
        rsi_f = float(rsi)
        price_f = float(price)
        below_emas = (
            (ema_20 is None or price_f < float(ema_20))
            and (ema_50 is None or price_f < float(ema_50))
        )
        above_emas = (
            (ema_20 is None or price_f > float(ema_20))
            and (ema_50 is None or price_f > float(ema_50))
        )
        if (
            direction == "short"
            and rsi_f <= 35
            and below_emas
            or direction == "long"
            and rsi_f >= 65
            and above_emas
        ):
            squeeze_risk = "against_trade"

    status, reasons, pressure = _observer_thesis_health(
        current_r=current_r,
        max_favorable_r=max_favorable_r,
        max_adverse_r=max_adverse_r,
        momentum_state=momentum_state,
        cvd_state=cvd_state,
        volume_followthrough=volume_followthrough,
        squeeze_risk=squeeze_risk,
    )
    return {
        "entry_quality": {
            "entry_zone_low": zone_low,
            "entry_zone_high": zone_high,
            "entry_vs_zone": _entry_zone_location(entry, zone_low, zone_high),
            "entry_to_zone_low_atr": _atr_distance(entry, zone_low, atr_f),
            "entry_to_zone_high_atr": _atr_distance(entry, zone_high, atr_f),
        },
        "excursion": {
            "risk_per_unit_pct": round(risk_per_unit, 6),
            "current_r": current_r,
            "mfe_r": max_favorable_r,
            "mae_r": max_adverse_r,
            "bars_since_entry": bars_since_entry,
            "bars_without_positive_mfe": bars_without_positive_mfe,
        },
        "thesis_health": {
            "status": status,
            "reasons": reasons,
            "directional_momentum": momentum_state,
            "cvd_agreement": cvd_state,
            "volume_followthrough": volume_followthrough,
            "squeeze_risk": squeeze_risk,
        },
        "recommended_pressure": pressure,
    }


@dataclass(frozen=True)
class ExitPolicy:
    """Concrete exit knobs carried by a trade after entry."""

    regime_cell: str | None
    exit_mode: str
    scale_out_frac: float
    trail_atr_mult: float
    breakeven_requires_tp1: bool
    trail_after_tp1_only: bool
    early_stop_mode: str
    early_trail_arm_atr: float
    early_trail_atr_mult: float


def _is_sideways(regime_cell: str | None) -> bool:
    return bool(regime_cell and regime_cell.split("-", 1)[0].lower() == "side")


def _exit_policy_for_regime(regime_cell: str | None) -> ExitPolicy:
    if _is_sideways(regime_cell) and settings.sideways_exit_mode == "range":
        return ExitPolicy(
            regime_cell=regime_cell,
            exit_mode="range",
            scale_out_frac=settings.sideways_scale_out_frac,
            trail_atr_mult=settings.sideways_trail_atr_mult,
            breakeven_requires_tp1=settings.sideways_breakeven_requires_tp1,
            trail_after_tp1_only=settings.sideways_trail_after_tp1_only,
            early_stop_mode=settings.sideways_early_stop_mode,
            early_trail_arm_atr=settings.sideways_early_trail_arm_atr,
            early_trail_atr_mult=settings.sideways_early_trail_atr_mult,
        )
    return ExitPolicy(
        regime_cell=regime_cell,
        exit_mode="trend",
        scale_out_frac=settings.scale_out_frac,
        trail_atr_mult=settings.trail_atr_mult,
        breakeven_requires_tp1=False,
        trail_after_tp1_only=False,
        early_stop_mode=settings.early_stop_mode,
        early_trail_arm_atr=settings.early_trail_arm_atr,
        early_trail_atr_mult=settings.early_trail_atr_mult,
    )


def _exit_policy_metadata(policy: ExitPolicy) -> dict[str, Any]:
    return {
        "regime_cell": policy.regime_cell,
        "exit_mode": policy.exit_mode,
        "exit_scale_out_frac": policy.scale_out_frac,
        "exit_trail_atr_mult": policy.trail_atr_mult,
        "exit_breakeven_requires_tp1": policy.breakeven_requires_tp1,
        "exit_trail_after_tp1_only": policy.trail_after_tp1_only,
        "exit_early_stop_mode": policy.early_stop_mode,
        "exit_early_trail_arm_atr": policy.early_trail_arm_atr,
        "exit_early_trail_atr_mult": policy.early_trail_atr_mult,
    }


def _exit_policy_from_metadata(md: dict[str, Any] | None) -> ExitPolicy:
    md = md or {}
    mode = md.get("exit_mode")
    if mode in {"range", "trend"}:
        return ExitPolicy(
            regime_cell=md.get("regime_cell"),
            exit_mode=str(mode),
            scale_out_frac=float(md.get("exit_scale_out_frac", settings.scale_out_frac)),
            trail_atr_mult=float(md.get("exit_trail_atr_mult", settings.trail_atr_mult)),
            breakeven_requires_tp1=bool(md.get("exit_breakeven_requires_tp1", False)),
            trail_after_tp1_only=bool(md.get("exit_trail_after_tp1_only", False)),
            early_stop_mode=str(md.get("exit_early_stop_mode", settings.early_stop_mode)),
            early_trail_arm_atr=float(
                md.get("exit_early_trail_arm_atr", settings.early_trail_arm_atr)
            ),
            early_trail_atr_mult=float(
                md.get("exit_early_trail_atr_mult", settings.early_trail_atr_mult)
            ),
        )
    regime_cell = md.get("regime_cell")
    return _exit_policy_for_regime(str(regime_cell) if regime_cell else None)


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


async def _open_trades_for(
    session: AsyncSession, symbol: str, *, run_id: str | None = None
) -> list[PaperTrade]:
    stmt = select(PaperTrade).where(PaperTrade.symbol == symbol, PaperTrade.status == "open")
    if run_id is not None:
        stmt = stmt.where(PaperTrade.run_id == run_id)
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
    md0 = trade.trade_metadata or {}
    policy = _exit_policy_from_metadata(md0)
    if settings.max_hold_bars <= 0:
        expires_at: datetime | None = None  # time-stop disabled — trades run to SL/TP
    else:
        raw_expiry = md0.get("expires_at")
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
    if policy.exit_mode == "range":
        report.notes.append(
            "exit_policy "
            f"trade={str(trade.trade_id)[:8]} mode={policy.exit_mode} "
            f"early={policy.early_stop_mode} "
            f"be_after_tp1={policy.breakeven_requires_tp1} "
            f"trail_atr={policy.trail_atr_mult:g} tp1_hit={state.tp_index > 0}"
        )
    # Review-gate the time-stop only when the exit manager is actually available.
    can_review = (
        settings.expiry_review_enabled
        and client is not None
        and settings.observe_enabled
        and feature_row is not None
    )
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
            scale_out_frac=policy.scale_out_frac,
            trail_atr_mult=policy.trail_atr_mult,
            atr=atr,
            early_stop_mode=policy.early_stop_mode,
            early_trail_arm_atr=policy.early_trail_arm_atr,
            early_trail_atr_mult=policy.early_trail_atr_mult,
            breakeven_arm_atr=settings.breakeven_arm_atr,
            breakeven_arm_cost_mult=settings.breakeven_arm_cost_mult,
            breakeven_requires_tp1=policy.breakeven_requires_tp1,
            trail_after_tp1_only=policy.trail_after_tp1_only,
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
                session, client, trade, trade_d, candle, state, report,
                feature_row=feature_row, policy=policy,
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
        if step.notes:
            report.notes.extend(step.notes)
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
                session, client, trade, trade_d, candle, state, report,
                feature_row=feature_row, policy=policy, bars=bars, cadence_call=True,
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
    run_id: str | None = None,
) -> None:
    """Advance each open trade. Uses ``fine_candles`` (finer-tf) when given, else this bar."""
    candles = fine_candles if fine_candles else [candle]
    for trade in await _open_trades_for(session, symbol, run_id=run_id):
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
    policy: ExitPolicy,
    bars: int = 0,
    cadence_call: bool = False,
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

    unrealized_notional = state.realized_pnl_pct + state.remaining_frac * leg_pnl(close)
    current_leg_notional = leg_pnl(close)
    unrealized_margin = _margin_pnl_pct(trade, unrealized_notional)
    current_leg_margin = _margin_pnl_pct(trade, current_leg_notional)
    max_favorable_margin = _margin_pnl_pct(trade, state.max_favorable_pnl_pct)
    max_adverse_margin = _margin_pnl_pct(trade, state.max_adverse_pnl_pct)
    notional_usd, margin_usd = _trade_notional_margin(trade)
    hold = _hold_context(trade, ts)
    stale_candidate = (
        (hold.get("current_window_progress") or hold.get("hold_progress") or 0.0)
        >= settings.observe_stale_hold_progress
        and abs(unrealized_margin) <= settings.observe_stale_unrealized_abs_pct
        and max_favorable_margin <= settings.observe_stale_mfe_pct
        and not state.breakeven
    )
    observer_context = _observer_context(
        trade=trade,
        trade_d=trade_d,
        feature_row=feature_row,
        hold=hold,
        current_leg_notional=current_leg_notional,
        max_favorable_notional=state.max_favorable_pnl_pct,
        max_adverse_notional=state.max_adverse_pnl_pct,
    )
    # Event-driven gate (#5): on the per-bar cadence call, only spend an LLM observation when
    # the deterministic thesis health says it matters. Deterministic exit management already
    # ran this bar in step_trade; a healthy, traveling trade needs no LLM "hold". The
    # expiry-review call (cadence_call=False) is never gated — that IS the event.
    if cadence_call and settings.observe_only_on_health:
        status = (observer_context.get("thesis_health") or {}).get("status")
        if not _observer_call_due(status, stale_candidate=stale_candidate, bars=bars):
            return False
    env = {
        "now": ts,
        "symbol": trade.symbol,
        "direction": direction,
        "entry_price": entry,
        "entry_time": trade.entry_time,
        "current_price": close,
        "unrealized_pct": round(unrealized_margin, 6),
        "unrealized_margin_pct": round(unrealized_margin, 6),
        "position_value": {
            "margin_usd": round(margin_usd, 2),
            "notional_usd": round(notional_usd, 2),
            "leverage": (
                round(notional_usd / margin_usd, 6)
                if margin_usd > 0
                else None
            ),
        },
        "working_stop": cur_ws,
        "take_profit": list(trade_d["take_profit"]),
        "remaining_frac": state.remaining_frac,
        "breakeven": state.breakeven,
        "trail_armed": state.trail_armed,
        "hold": hold,
        "progress": {
            "pnl_basis": "margin",
            "current_leg_pnl_pct": round(current_leg_margin, 6),
            "max_favorable_pnl_pct": round(max_favorable_margin, 6),
            "max_adverse_pnl_pct": round(max_adverse_margin, 6),
            "stale_candidate": stale_candidate,
            "stale_thresholds": {
                "hold_progress": settings.observe_stale_hold_progress,
                "unrealized_abs_pct": settings.observe_stale_unrealized_abs_pct,
                "max_favorable_pnl_pct": settings.observe_stale_mfe_pct,
            },
        },
        "observer_context": observer_context,
        "exit_policy": _exit_policy_metadata(policy),
        "observe_timeframe": settings.observe_timeframe,
        "features_now": feature_row,
    }
    obs, llm = await client.observe_trade(env, symbol=trade.symbol)
    report.observe_calls += 1
    trace.observe(
        now=ts,
        trade_id=trade.trade_id,
        plan_id=trade.plan_id,
        action=obs.action if llm.parse_ok and obs is not None else "PARSE_FAIL",
        parse_ok=llm.parse_ok,
        price=close,
        unrealized_pct=unrealized_margin,
        working_stop=cur_ws,
        remaining_frac=state.remaining_frac,
        confidence=float(obs.confidence) if obs is not None else None,
        reason=obs.reason if obs is not None else None,
        new_stop=float(obs.new_stop) if obs is not None and obs.new_stop is not None else None,
        new_tp=list(obs.new_tp) if obs is not None and obs.new_tp else None,
        scale_frac=float(obs.scale_frac) if obs is not None and obs.scale_frac else None,
    )
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
            state.working_stop = breakeven_stop(direction, entry, cost_bps)
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
    *,
    run_id: str | None = None,
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
            for trade in await _open_trades_for(session, s.symbol, run_id=run_id):
                if trade.setup_id == s.setup_id:
                    direction = trade.direction
                    entry = _f(trade.entry_price)
                    st = TradeState.from_metadata(trade.trade_metadata)
                    # A stopped-out trade can never fill worse than its (working) stop: if a
                    # price-level invalidation resolves beyond the stop, reality would have
                    # filled the stop first. Bound the exit so invalidation can't print a loss
                    # larger than a stop-out at this level.
                    ws = st.working_stop if st.working_stop is not None else _f(trade.stop_loss)
                    price = feature_row["price"]
                    price = max(price, ws) if direction == "long" else min(price, ws)
                    raw = (price - entry) / entry if entry else 0.0
                    leg = raw if direction == "long" else -raw
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
            for trade in await _open_trades_for(session, s.symbol, run_id=run_id):
                if trade.setup_id != s.setup_id or trade.status != "open":
                    continue
                st = TradeState.from_metadata(trade.trade_metadata)
                if not st.breakeven:
                    cost_bps = settings.fee_bps + settings.slippage_bps
                    st.working_stop = breakeven_stop(
                        trade.direction, _f(trade.entry_price), cost_bps
                    )
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
            "trigger_price": ev.trigger_price,
            "close_price": ev.close_price,
            "entry_trigger_mode": settings.entry_trigger_mode,
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
                reason=(
                    "parse_fail_fallback: deterministic signal strong; "
                    "executing at reduced size"
                ),
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

    policy = _exit_policy_for_regime(plan.regime_cell)
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
    if trade is not None:
        md = _exit_policy_metadata(policy)
        md.update(
            entry_zone_low=_f(setup.entry_zone_low),
            entry_zone_high=_f(setup.entry_zone_high),
        )
        if settings.max_hold_bars > 0:
            hold = timeframe_to_timedelta(tf) * settings.max_hold_bars
            md.update(
                expires_at=(now + hold).isoformat(),
                # Window length each thesis-review extension grants (see _advance_trade).
                hold_window_seconds=hold.total_seconds(),
            )
        trade.trade_metadata = md
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
    trace.outcome(
        "EXIT POLICY "
        f"mode={policy.exit_mode} be_after_tp1={policy.breakeven_requires_tp1} "
        f"trail_atr={policy.trail_atr_mult:g}"
    )
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
    run_id: str | None = None,
) -> TickReport:
    """Run one full evaluation of the active plan against the current feature row.

    When ``fine_candles`` is supplied (a position is open and a finer timeframe is
    configured), open trades are reconciled bar-by-bar over those candles with the
    observation agent in the loop; otherwise the single decision-bar candle is used.
    ``run_id`` isolates replay runs so concurrent same-symbol replays don't see each
    other's plans or trades.
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
        run_id=run_id,
    )

    plan = await state.active_plan(session, symbol, run_id=run_id)
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
        session, client, plan, setups, feature_row, prev_row, candle_closed, report,
        run_id=run_id,
    )
    if killed or report.paused:
        return report

    open_positions = await state.open_positions(session, symbol=symbol, run_id=run_id)
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
        ev = evaluate_setup(
            setup_d,
            feature_row,
            prev_row,
            soft_threshold=settings.soft_threshold,
            entry_trigger_mode=settings.entry_trigger_mode,
        )
        if not ev.detected:
            continue
        report.detections += 1
        # Enforce the direction gate the strategist was given. The planner persists the
        # exact allowed_directions it planned under (including any HTF-exhaustion counter-
        # trend relief) onto the plan, so honor that; fall back to the strict trend-only
        # gate for plans that predate the persisted field.
        allowed = (getattr(plan, "plan_metadata", None) or {}).get("allowed_directions")
        if allowed is not None:
            direction_ok = setup.direction in allowed
        else:
            direction_ok = regime_allows(plan.regime_cell, setup.direction)
        if settings.regime_filter and not direction_ok:
            trace.outcome(f"SKIPPED: regime {plan.regime_cell} disallows {setup.direction}")
            report.notes.append(
                f"setup {setup.setup_id} regime-filtered ({plan.regime_cell}/{setup.direction})"
            )
            continue
        if settings.entry_confirmation_enabled:
            confirmed, reason = entry_confirmed(setup.direction, feature_row, prev_row)
            if not confirmed:
                trace.outcome(f"AWAITING confirmation: {reason}")
                report.notes.append(
                    f"setup {setup.setup_id} awaiting confirmation ({reason})"
                )
                continue
        opened_before = report.opened
        await _confirm_and_execute(
            session, client, plan, setup, setup_d, ev, feature_row, now, open_positions, report,
            tf=tf,
        )
        if report.opened > opened_before:
            break

    await session.flush()
    return report
