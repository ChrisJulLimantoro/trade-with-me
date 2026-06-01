"""create_plan orchestration — the strategist step.

Builds a structured market envelope from the latest features/regime/candles, asks the
LLM client (mock or real) for a plan, validates it, and persists Plan + Setups + an
LlmCall audit row. Supersedes any prior active plan for the symbol so only one plan is
active at a time (plan versioning).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ats.config import settings
from ats.db.models import LlmCall, Plan, Setup
from ats.engine import state
from ats.engine.timeframes import timeframe_to_timedelta
from ats.llm.client import LlmClient
from ats import trace
from ats.llm.schemas import LlmResult, PlanOutput, SetupOutput
from ats.logging import get_logger
from ats.risk.manager import reward_risk

log = get_logger(__name__)


def _worst_case_fill(setup: SetupOutput) -> float:
    """The entry-zone edge the engine treats as worst case for reward:risk.

    The engine may fill anywhere inside entry_zone and computes RR at the actual
    fill, so the unfavorable edge is the one nearest the take-profit: the high edge
    for longs, the low edge for shorts.
    """
    low, high = setup.entry_zone
    return high if setup.direction == "long" else low


def _admissible_setups(setups: list[SetupOutput], *, min_rr: float) -> list[SetupOutput]:
    """Drop setups whose worst-case-fill RR cannot clear ``min_rr``.

    This mirrors the deterministic risk gate (``risk.manager.assess``) so doomed setups
    never reach the confirm stage and burn LLM calls. A setup that clears here may still
    be rejected at fill time if price moves, but it can no longer be structurally
    impossible.
    """
    admissible: list[SetupOutput] = []
    for s in setups:
        rr = reward_risk(s.direction, _worst_case_fill(s), s.stop_loss, list(s.take_profit))
        if rr < min_rr:
            log.info(
                "setup_dropped_low_rr",
                direction=s.direction,
                worst_case_rr=round(rr, 3),
                min_rr=min_rr,
                entry_zone=s.entry_zone,
                stop_loss=s.stop_loss,
                tp1=s.take_profit[0] if s.take_profit else None,
            )
            continue
        admissible.append(s)
    return admissible


@dataclass
class PlanResult:
    plan: Plan | None
    setups: list[Setup]
    llm: LlmResult


def _json_safe(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))


async def build_envelope(
    session: AsyncSession,
    symbol: str,
    tf: str,
    feature_row: dict[str, Any],
    *,
    as_of: datetime,
) -> dict[str, Any]:
    """Assemble the JSON-only context passed to create_plan."""
    regime = await state.latest_regime(session, before_ts=as_of)
    candles = await state.recent_candles(session, symbol, tf, as_of, n=50)
    positions = await state.open_positions(session)
    return {
        "as_of": as_of,
        "symbol": symbol,
        "timeframe": tf,
        "features": feature_row,
        "regime": regime,
        "recent_ohlcv": candles,
        "portfolio": {
            "equity_usd": settings.paper_equity_usd,
            "open_positions": positions,
        },
        "risk_limits": {
            "max_position_pct": settings.max_position_pct,
            "min_rr": settings.min_rr,
            "one_position_per_symbol": True,
        },
    }


async def _persist(
    session: AsyncSession,
    *,
    symbol: str,
    tf: str,
    as_of: datetime,
    plan_out: PlanOutput,
    regime_cell: str | None,
) -> tuple[Plan, list[Setup]]:
    # Supersede any currently-active plan for this symbol (plan versioning).
    await session.execute(
        update(Plan)
        .where(Plan.symbol == symbol, Plan.status == "active")
        .values(status="superseded")
    )

    ttl = timeframe_to_timedelta(tf) * settings.plan_refresh_bars
    expires_at = as_of + ttl
    plan = Plan(
        plan_id=uuid.uuid4(),
        symbol=symbol,
        expires_at=expires_at,
        as_of=as_of,
        market_bias=plan_out.market_bias,
        status="active",
        regime_cell=regime_cell,
        rationale=plan_out.rationale,
        plan_metadata={"timeframe": tf},
    )
    session.add(plan)

    setups: list[Setup] = []
    for s in _admissible_setups(plan_out.allowed_setups, min_rr=settings.min_rr):
        setup = Setup(
            setup_id=uuid.uuid4(),
            plan_id=plan.plan_id,
            symbol=symbol,
            direction=s.direction,
            status="active",
            entry_zone_low=s.entry_zone[0],
            entry_zone_high=s.entry_zone[1],
            take_profit=list(s.take_profit),
            stop_loss=s.stop_loss,
            size_pct=s.size_pct,
            hard_rules=[r.model_dump() for r in s.hard_rules],
            soft_rules=[r.model_dump() for r in s.soft_rules],
            invalidation_rules=[r.model_dump() for r in s.invalidation_rules],
            expires_at=expires_at,
        )
        session.add(setup)
        setups.append(setup)
    await session.flush()
    return plan, setups


async def create_plan(
    session: AsyncSession,
    client: LlmClient,
    *,
    symbol: str,
    tf: str,
    as_of: datetime | None = None,
) -> PlanResult:
    """Run one create_plan cycle. Returns the persisted plan (or None on LLM failure)."""
    feature_row = await state.latest_feature_row(session, symbol, tf, as_of=as_of)
    if feature_row is None:
        raise ValueError(f"no features for {symbol} {tf} (run `ats process backfill` first)")
    effective_as_of = as_of or feature_row["open_time"]

    envelope = await build_envelope(session, symbol, tf, feature_row, as_of=effective_as_of)
    plan_out, llm = await client.create_plan(envelope, symbol=symbol)

    plan: Plan | None = None
    setups: list[Setup] = []
    if llm.parse_ok and plan_out is not None:
        regime_cell = (envelope.get("regime") or {}).get("regime_cell")
        plan, setups = await _persist(
            session,
            symbol=symbol,
            tf=tf,
            as_of=effective_as_of,
            plan_out=plan_out,
            regime_cell=regime_cell,
        )
        trace.plan(plan, setups)
    else:
        log.warning("create_plan_llm_failed", symbol=symbol, model=llm.model)

    session.add(
        LlmCall(
            call_id=uuid.uuid4(),
            kind="create_plan",
            model=llm.model,
            mock=llm.mock,
            symbol=symbol,
            plan_id=plan.plan_id if plan else None,
            input_tokens=llm.input_tokens,
            output_tokens=llm.output_tokens,
            cost_usd=llm.cost_usd,
            latency_ms=llm.latency_ms,
            parse_ok=llm.parse_ok,
            request=_json_safe(envelope),
            response=llm.raw,
        )
    )
    await session.flush()
    return PlanResult(plan=plan, setups=setups, llm=llm)
