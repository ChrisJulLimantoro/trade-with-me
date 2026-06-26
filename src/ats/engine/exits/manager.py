"""Deterministic exit machine driver.

Walks each open trade across a sequence of candles, stepping the deterministic exit machine
(stop / target / scale-out / trail / breakeven / time-stop) and, on the finer-tf path,
event-gating the tactical observer. The only LLM influence here is delegated to the observer
at the time-stop review and the per-bar cadence.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ats.config import settings
from ats.db.models import PaperTrade
from ats.engine.closing import close_trade, open_trades_for
from ats.engine.exits.policy import exit_policy_from_metadata
from ats.engine.observer import observe_and_adjust
from ats.engine.quantities import _f
from ats.engine.reports import TickReport
from ats.execution.executor import record_partial_exit, update_trade_state
from ats.execution.reconcile import (
    ExitResult,
    TradeState,
    _pnl_pct,
    net_of_costs,
    step_trade,
)
from ats.llm.client import LlmClient
from ats.logging import get_logger

log = get_logger(__name__)


async def advance_trade(
    session: AsyncSession,
    client: LlmClient | None,
    trade: PaperTrade,
    candles: list[dict[str, Any]],
    report: TickReport,
    *,
    atr: float | None,
    feature_row: dict[str, Any] | None,
    intrabar: bool = False,
) -> None:
    """Walk one open trade across a sequence of candles (finer-tf bars or a single bar).

    Each candle: step the deterministic exit machine (stop / target / scale-out / trail /
    expiry). Every ``observe_every_bars`` candles, consult the observation agent and apply
    its (code-clamped) adjustment. Stops on the first full close.
    """
    md0 = trade.trade_metadata or {}
    policy = exit_policy_from_metadata(md0)
    if settings.exits.max_hold_bars <= 0:
        expires_at: datetime | None = None  # time-stop disabled — trades run to SL/TP
    else:
        raw_expiry = md0.get("expires_at")
        last_ts = candles[-1]["open_time"] if candles else trade.entry_time
        expires_at = (
            datetime.fromisoformat(raw_expiry) if isinstance(raw_expiry, str) else last_ts
        )
    # Conditional loser-cut threshold: the bar after which a still-red, unprotected trade has its
    # stop ratcheted tighter (SPEC3-LOOP / Iter 3). ``entry + frac*(expires_at - entry)`` ==
    # ``entry + frac*hold_window`` and needs no timeframe lookup. None when disabled.
    loss_cut_at: datetime | None = None
    if (
        expires_at is not None
        and settings.exits.loss_cut_hold_frac > 0
        and settings.exits.loss_cut_atr_mult > 0
    ):
        loss_cut_at = trade.entry_time + settings.exits.loss_cut_hold_frac * (
            expires_at - trade.entry_time
        )
    trade_d = {
        "direction": trade.direction,
        "entry_price": _f(trade.entry_price),
        "stop_loss": _f(trade.stop_loss),
        "take_profit": [float(x) for x in trade.take_profit],
    }
    state = TradeState.from_metadata(trade.trade_metadata)
    liq_price = _f(trade.liq_price) if trade.liq_price is not None else None
    cost_bps = settings.risk.fee_bps + settings.risk.slippage_bps
    if policy.exit_mode == "range":
        report.notes.append(
            "exit_policy "
            f"trade={str(trade.trade_id)[:8]} mode={policy.exit_mode} "
            f"early={policy.early_stop_mode} "
            f"be_after_tp1={policy.breakeven_requires_tp1} "
            f"trail_atr={policy.trail_atr_mult:g} trail={policy.trail_mode} "
            f"tp1_hit={state.tp_index > 0}"
        )
    # Review-gate the time-stop only when the exit manager is actually available.
    can_review = (
        settings.exits.expiry_review_enabled
        and client is not None
        and settings.observer.observe_enabled
        and feature_row is not None
    )
    extensions = int(md0.get("hold_extensions", 0))
    window_s = float(md0.get("hold_window_seconds", 0.0))
    # Within-symbol vol-conditional trail: on the trade's own high-pr_atr (expanded/trend) bars,
    # loosen the runner trail so winners ride further; quiet (chop) bars keep the tight base. Uses
    # the causal trailing percentile from the current feature row; the trail only ratchets, so a
    # widen never loosens an already-locked stop. Falls back to the policy trail when disabled.
    trail_atr_mult = policy.trail_atr_mult
    if settings.exits.trail_atr_mult_trend > 0.0 and feature_row is not None:
        pr_atr = _f(feature_row.get("pr_atr"))
        if pr_atr is not None and pr_atr >= settings.exits.trail_trend_pr_atr_min:
            trail_atr_mult = settings.exits.trail_atr_mult_trend
    bars = 0
    for candle in candles:
        if trade.entry_time >= candle["open_time"]:
            continue  # entered this bar or later — can't exit on its own entry bar
        step = step_trade(
            trade_d,
            candle,
            state,
            expires_at=expires_at,
            loss_cut_at=loss_cut_at,
            loss_cut_atr_mult=settings.exits.loss_cut_atr_mult,
            scale_out_frac=policy.scale_out_frac,
            trail_atr_mult=trail_atr_mult,
            trail_mode=policy.trail_mode,
            atr=atr,
            early_stop_mode=policy.early_stop_mode,
            early_trail_arm_atr=policy.early_trail_arm_atr,
            early_trail_atr_mult=policy.early_trail_atr_mult,
            breakeven_arm_atr=settings.exits.breakeven_arm_atr,
            breakeven_arm_cost_mult=settings.exits.breakeven_arm_cost_mult,
            breakeven_lock_atr=settings.exits.breakeven_lock_atr,
            breakeven_requires_tp1=policy.breakeven_requires_tp1,
            trail_after_tp1_only=policy.trail_after_tp1_only,
            cost_bps=cost_bps,
            review_on_expiry=can_review,
            liq_price=liq_price,
            # On the finer-tf walk, close-confirm the unprotected base stop so a sub-bar wick
            # can't pre-empt the bar-close invalidation/structure guard (defect 0.2).
            stop_on_close=intrabar,
        )
        if step.closed:
            await close_trade(session, client, trade, step.exit_result, report, atr_at_close=atr)
            return
        if step.expiry_due:
            # Time-stop reached: review the thesis instead of a blind close. A clear reversal
            # closes via EXIT_NOW; otherwise grant one more full hold window, up to a backstop.
            if await observe_and_adjust(
                session, client, trade, trade_d, candle, state, report,
                feature_row=feature_row, policy=policy,
            ):
                return  # exit manager closed it (EXIT_NOW)
            if extensions < settings.exits.max_hold_extensions and window_s > 0:
                extensions += 1
                expires_at = candle["open_time"] + timedelta(seconds=window_s)
                new_md = dict(trade.trade_metadata or {})
                new_md.update(expires_at=expires_at.isoformat(), hold_extensions=extensions)
                trade.trade_metadata = new_md
                await session.flush()
                report.notes.append(
                    f"hold extended {extensions}/{settings.exits.max_hold_extensions} "
                    "(thesis intact)"
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
            log.info(
                "time_stop_fired",
                trade_id=str(trade.trade_id),
                bars_held=bars,
                extensions=extensions,
                pnl_pct=round(total, 6),
            )
            await close_trade(
                session, client, trade,
                ExitResult(close_px, candle["open_time"], "expiry", total),
                report, atr_at_close=atr,
            )
            return
        if step.partial is not None:
            await record_partial_exit(
                session, trade.trade_id, step.partial, step.state,
                equity_usd=settings.risk.paper_equity_usd, size_pct=_f(trade.size_pct),
            )
        elif step.state.working_stop != state.working_stop:
            await update_trade_state(session, trade.trade_id, step.state)
        if step.notes:
            report.notes.extend(step.notes)
        state = step.state
        bars += 1

        if (
            client is not None
            and settings.observer.observe_enabled
            and feature_row is not None
            and len(candles) > 1  # only on the finer-tf path
            and bars % settings.observer.observe_every_bars == 0
        ):
            closed = await observe_and_adjust(
                session, client, trade, trade_d, candle, state, report,
                feature_row=feature_row, policy=policy, bars=bars, cadence_call=True,
            )
            if closed:
                return


async def reconcile_open_trades(
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
    intrabar = fine_candles is not None
    candles = fine_candles if fine_candles else [candle]
    for trade in await open_trades_for(session, symbol, run_id=run_id):
        await advance_trade(
            session, client, trade, candles, report, atr=atr, feature_row=feature_row,
            intrabar=intrabar,
        )
