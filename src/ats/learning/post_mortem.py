"""Post-mortem: on a closed trade, write one structured ``learning`` row.

Called from the engine's single close path. Builds a JSON envelope of the closed trade +
the market context at entry and exit, asks the reflect_trade agent for a structured lesson,
fingerprints the entry conditions, and persists the learning (plus an llm_calls audit row).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ats import trace
from ats.db.models import LlmCall, PaperTrade, Plan, Setup
from ats.engine import state
from ats.execution.reconcile import ExitResult
from ats.learning.fingerprint import build_fingerprint, to_vector_literal
from ats.llm.client import LlmClient
from ats.logging import get_logger

log = get_logger(__name__)


def _outcome(pnl: float) -> str:
    return "win" if pnl > 0 else "loss" if pnl < 0 else "flat"


async def reflect_and_store(
    session: AsyncSession,
    client: LlmClient,
    trade: PaperTrade,
    exit_result: ExitResult,
) -> None:
    """Run the post-mortem for ``trade`` and persist a learning + audit row."""
    plan = await session.get(Plan, trade.plan_id)
    setup = await session.get(Setup, trade.setup_id)
    tf = str((plan.plan_metadata or {}).get("timeframe", "15m")) if plan else "15m"
    regime_cell = plan.regime_cell if plan else None

    entry_row = await state.latest_feature_row(session, trade.symbol, tf, as_of=trade.entry_time)
    exit_row = await state.latest_feature_row(
        session, trade.symbol, tf, as_of=exit_result.exit_time
    )

    envelope: dict[str, Any] = {
        "trade": {
            "direction": trade.direction,
            "entry_price": float(trade.entry_price),
            "entry_time": trade.entry_time,
            "exit_price": exit_result.exit_price,
            "exit_time": exit_result.exit_time,
            "exit_reason": exit_result.exit_reason,
            "pnl_pct": exit_result.pnl_pct,
            "size_pct": float(trade.size_pct),
            "stop_loss": float(trade.stop_loss),
            "take_profit": [float(x) for x in trade.take_profit],
        },
        "setup": {
            "hard_rules": setup.hard_rules if setup else [],
            "soft_rules": setup.soft_rules if setup else [],
            "invalidation_rules": setup.invalidation_rules if setup else [],
            "entry_zone": (
                [float(setup.entry_zone_low), float(setup.entry_zone_high)] if setup else None
            ),
        },
        "regime_cell": regime_cell,
        "features_at_entry": entry_row,
        "features_at_exit": exit_row,
    }

    reflection, llm = await client.reflect_trade(envelope, symbol=trade.symbol)

    session.add(
        LlmCall(
            call_id=uuid.uuid4(),
            kind="reflect_trade",
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

    if not llm.parse_ok or reflection is None:
        log.warning("reflect_parse_failed", trade_id=str(trade.trade_id), model=llm.model)
        return

    fingerprint = build_fingerprint(entry_row or {}, {"regime_cell": regime_cell})
    await session.execute(
        text(
            "INSERT INTO learnings ("
            "  id, trade_id, symbol, direction, regime_cell, category, hypothesis, evidence, "
            "  proposed_adjustment, confidence_in_lesson, outcome, pnl_pct, fingerprint"
            ") VALUES ("
            "  :id, :trade_id, :symbol, :direction, :regime_cell, :category, :hypothesis, "
            "  :evidence, :proposed_adjustment, :confidence, :outcome, :pnl_pct, "
            "  CAST(:fingerprint AS vector)"
            ")"
        ),
        {
            "id": uuid.uuid4(),
            "trade_id": trade.trade_id,
            "symbol": trade.symbol,
            "direction": trade.direction,
            "regime_cell": regime_cell,
            "category": reflection.category,
            "hypothesis": reflection.hypothesis,
            "evidence": reflection.evidence,
            "proposed_adjustment": reflection.proposed_adjustment,
            "confidence": reflection.confidence_in_lesson,
            "outcome": _outcome(exit_result.pnl_pct),
            "pnl_pct": exit_result.pnl_pct,
            "fingerprint": to_vector_literal(fingerprint),
        },
    )
    await session.flush()
    log.info(
        "learning_stored",
        trade_id=str(trade.trade_id),
        category=reflection.category,
        outcome=_outcome(exit_result.pnl_pct),
    )
    trace.learning(
        trade_id=trade.trade_id,
        category=reflection.category,
        outcome=_outcome(exit_result.pnl_pct),
        pnl_pct=exit_result.pnl_pct,
        hypothesis=reflection.hypothesis,
        proposed_adjustment=reflection.proposed_adjustment,
        confidence=reflection.confidence_in_lesson,
    )
