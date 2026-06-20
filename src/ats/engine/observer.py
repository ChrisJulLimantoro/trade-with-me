"""Tactical LLM exit manager (the observer).

Builds the deterministic thesis-health context for an open trade, decides whether an LLM
observation is worth spending this bar (event gate #5), calls the observation agent, and
applies its adjustment through the risk-only clamps so the LLM can only protect/extend,
never widen risk. The only LLM call on the trade lifecycle lives here.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ats import trace
from ats.config import settings
from ats.db.models import LlmCall, PaperTrade
from ats.engine.closing import close_trade
from ats.engine.exits.policy import (
    ExitPolicy,
    clamp_extended_tp,
    clamp_tightened_stop,
    exit_policy_metadata,
)
from ats.engine.quantities import (
    _f,
    _margin_pnl_pct,
    _parse_dt,
    _round_or_none,
    _trade_notional_margin,
)
from ats.engine.reports import TickReport
from ats.execution.executor import record_partial_exit, update_trade_state
from ats.execution.reconcile import (
    ExitResult,
    PartialFill,
    TradeState,
    _pnl_pct,
    breakeven_stop,
    net_of_costs,
)
from ats.llm.client import LlmClient
from ats.logging import get_logger

log = get_logger(__name__)


def _hold_context(trade: PaperTrade, now: Any) -> dict[str, Any]:
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
        held_s / window_s * settings.exits.max_hold_bars
        if window_s and settings.exits.max_hold_bars > 0
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
        "max_hold_extensions": settings.exits.max_hold_extensions,
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


def observer_call_due(status: str | None, *, stale_candidate: bool, bars: int) -> bool:
    """Event-driven observer gate (#5): is an LLM observation worth spending this bar?

    Consult the observer when the deterministic thesis is decaying/broken, the trade is a
    stale candidate, or the periodic health fallback cadence has elapsed. A healthy, still-
    traveling trade is left to deterministic management (step_trade) and skips the LLM call.
    """
    if status in {"decaying", "broken"} or stale_candidate:
        return True
    fb = settings.observer.observe_health_fallback_bars
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


async def observe_and_adjust(
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
        >= settings.observer.observe_stale_hold_progress
        and abs(unrealized_margin) <= settings.observer.observe_stale_unrealized_abs_pct
        and max_favorable_margin <= settings.observer.observe_stale_mfe_pct
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
    if cadence_call and settings.observer.observe_only_on_health:
        status = (observer_context.get("thesis_health") or {}).get("status")
        if not observer_call_due(status, stale_candidate=stale_candidate, bars=bars):
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
                "hold_progress": settings.observer.observe_stale_hold_progress,
                "unrealized_abs_pct": settings.observer.observe_stale_unrealized_abs_pct,
                "max_favorable_pnl_pct": settings.observer.observe_stale_mfe_pct,
            },
        },
        "observer_context": observer_context,
        "exit_policy": exit_policy_metadata(policy),
        "observe_timeframe": settings.observer.observe_timeframe,
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

    cost_bps = settings.risk.fee_bps + settings.risk.slippage_bps
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
                equity_usd=settings.risk.paper_equity_usd, size_pct=_f(trade.size_pct),
            )
            trace.outcome(f"OBSERVE scale out {frac:.2f} @ {close:g} ({obs.reason})")
        return False

    if obs.action == "EXIT_NOW":
        conf = float(obs.confidence) if obs.confidence is not None else 0.0
        if conf >= settings.observer.observe_exit_min_conf:
            total = state.realized_pnl_pct + net_of_costs(
                state.remaining_frac, leg_pnl(close), cost_bps
            )
            raw_atr = feature_row.get("atr_14")
            await close_trade(
                session, client, trade,
                ExitResult(close, ts, "observe_exit", total), report,
                atr_at_close=float(raw_atr) if raw_atr is not None else None,
            )
            trace.outcome(f"OBSERVE exit now @ {close:g} (conf {conf:.2f}; {obs.reason})")
            return True
        trace.outcome(
            f"OBSERVE exit ignored (conf {conf:.2f} < {settings.observer.observe_exit_min_conf})"
        )
    return False
