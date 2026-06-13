"""create_plan orchestration — the strategist step.

Builds a structured market envelope from the latest features/regime/candles, asks the
LLM client (mock or real) for a plan, validates it, and persists Plan + Setups + an
LlmCall audit row. Supersedes any prior active plan for the symbol so only one plan is
active at a time (plan versioning).
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ats import trace
from ats.config import settings
from ats.db.models import LlmCall, Plan, Setup
from ats.engine import state
from ats.engine.timeframes import timeframe_to_timedelta
from ats.llm.client import LlmClient
from ats.llm.schemas import InvalidationRule, LlmResult, PlanOutput, SetupOutput
from ats.logging import get_logger
from ats.planning.context import build_planner_context
from ats.risk.manager import regime_allows, reward_risk

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


# How close a hard price-invalidation literal must sit to the stop to count as a duplicate
# of it (relative to the stop price).
_STOP_DUP_TOL = 0.002


def _stripped_invalidation_rules(s: SetupOutput) -> list[InvalidationRule]:
    """Drop hard invalidation rules that merely restate the stop level.

    The strategist routinely emits a ``[hard] price < stop`` (long) / ``price > stop`` (short)
    rule. That duplicates the protective stop the exit machine already enforces — but
    invalidation closes at the *bar close*, which on a fast bar prints past the stop and
    produces a loss larger than a clean stop-out. Reconciliation already fills the stop on the
    finer timeframe, and the invalidation exit is now stop-bounded, so these rules are at best
    redundant and at worst harmful. Keep invalidation for genuine thesis-level signals.
    """
    stop = s.stop_loss
    if stop <= 0:
        return list(s.invalidation_rules)
    kept: list[InvalidationRule] = []
    for r in s.invalidation_rules:
        is_price_literal = (
            r.severity == "hard" and r.left == "price" and isinstance(r.right, (int, float))
        )
        redundant = False
        if is_price_literal:
            level = float(r.right)
            if s.direction == "long" and r.operator in ("<", "<="):
                # Fires once price falls to/through a level at-or-below (worse than) the stop.
                redundant = level <= stop * (1 + _STOP_DUP_TOL)
            elif s.direction == "short" and r.operator in (">", ">="):
                redundant = level >= stop * (1 - _STOP_DUP_TOL)
        if redundant:
            log.info(
                "invalidation_rule_dropped_stop_dup",
                direction=s.direction,
                operator=r.operator,
                level=float(r.right),
                stop_loss=stop,
            )
            continue
        kept.append(r)
    return kept


def _stripped_hard_rules(s: SetupOutput) -> list[dict[str, Any]]:
    """Drop hard ENTRY rules that merely restate the entry_zone as a bare price literal.

    The strategist routinely re-encodes the entry band as hard rules (``price >= zone_low``,
    ``price <= zone_high``) — but the ``entry_zone`` already IS the executable price gate
    (``in_entry_zone`` / the wick-limit trigger). When the literal drifts from the zone edge
    it doesn't just duplicate the gate, it CONTRADICTS it: a ``price >= 61850`` hard rule on a
    setup whose zone fills at 61500 can never trigger, so the setup is permanently undetectable
    even when price sits squarely in the zone. (Measured: bare price-vs-literal rules were the
    single largest cause of in-zone setups that never filled.) Strip every hard rule that is
    ``price`` compared to a numeric literal; keep ``price`` vs a feature (e.g. ``price < ema_50``
    is a genuine trend gate, not a zone restatement) and all non-price rules.
    """
    kept: list[dict[str, Any]] = []
    for r in s.hard_rules:
        is_price_literal = (
            (r.left == "price" and isinstance(r.right, (int, float)))
            or (r.right == "price" and isinstance(r.left, (int, float)))
        )
        if is_price_literal:
            log.info(
                "hard_rule_dropped_zone_dup",
                direction=s.direction,
                left=r.left,
                operator=r.operator,
                right=r.right,
                entry_zone=s.entry_zone,
            )
            continue
        kept.append(r.model_dump())
    return kept


@dataclass
class PlanResult:
    plan: Plan | None
    setups: list[Setup]
    llm: LlmResult


def _json_safe(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))


def _prune_missing(row: dict[str, Any]) -> dict[str, Any]:
    """Drop feature keys whose value is None or NaN before sending to the strategist.

    A NaN/missing operand makes the rule engine silently evaluate to False, so an absent
    feature must never be advertised — otherwise the LLM may anchor a hard rule on a dead
    column and the setup becomes permanently undetectable. Columns that are NULL in the DB
    today (basis, OI delta) simply vanish from the envelope and reappear automatically once
    they are populated. Non-numeric fields (timestamps, strings) are always preserved.
    """
    pruned: dict[str, Any] = {}
    for k, v in row.items():
        if v is None:
            continue
        if isinstance(v, float) and math.isnan(v):
            continue
        pruned[k] = v
    return pruned


def _rule_operands(rules: list[Any]) -> set[str]:
    """Feature names referenced by a rule list (string operands, excluding ``price``)."""
    refs: set[str] = set()
    for r in rules:
        d = r.model_dump() if hasattr(r, "model_dump") else r
        for key in ("left", "right"):
            v = d.get(key)
            if isinstance(v, str) and v != "price":
                refs.add(v)
    return refs


def _warn_unknown_features(plan_out: PlanOutput, available: set[str], *, symbol: str) -> None:
    """Log (once per plan) any setup rule that references a feature absent from the envelope.

    The envelope is pruned of NaN/missing features, so a referenced name that isn't in
    ``available`` is one the LLM invented or one whose column is currently unavailable —
    either way the rule would silently evaluate False, so we surface it loudly here.
    """
    for s in plan_out.allowed_setups:
        refs = (
            _rule_operands(s.hard_rules)
            | _rule_operands(s.soft_rules)
            | _rule_operands(s.invalidation_rules)
        )
        unknown = sorted(refs - available)
        if unknown:
            log.warning(
                "setup_rule_unknown_feature",
                symbol=symbol,
                direction=s.direction,
                unknown=unknown,
            )


async def build_envelope(
    session: AsyncSession,
    symbol: str,
    tf: str,
    feature_row: dict[str, Any],
    *,
    as_of: datetime,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Assemble the JSON-only context passed to create_plan."""
    regime = await state.latest_regime(session, before_ts=as_of)
    candles = await state.recent_candles(session, symbol, tf, as_of, n=50)
    positions = await state.open_positions(session, run_id=run_id)

    # Derive which directions the regime allows so the strategist doesn't propose setups
    # that will immediately be discarded by the runtime regime filter.
    regime_cell: str | None = (regime or {}).get("regime_cell")
    allowed_directions: list[str] = []
    for d in ("long", "short"):
        if regime_allows(regime_cell, d):
            allowed_directions.append(d)

    # Higher-timeframe context: a read-only snapshot of slower charts for bias/direction
    # only (executable rules stay on the base tf). Look-ahead guard: an htf bar with
    # open_time T closes at T + htf_duration, so it is only fully formed at `as_of` when
    # T <= as_of - htf_duration. Calling the readers with that shifted timestamp selects
    # the most-recent CLOSED htf bar and never leaks a still-forming one during replay.
    higher_timeframes: dict[str, Any] = {}
    for htf in settings.context_timeframes:
        if htf == tf:
            continue
        htf_as_of = as_of - timeframe_to_timedelta(htf)
        htf_features = await state.latest_feature_row(session, symbol, htf, as_of=htf_as_of)
        if htf_features is None:
            log.warning("htf_context_missing", symbol=symbol, timeframe=htf)
            continue
        higher_timeframes[htf] = {
            "as_of": htf_as_of,
            "features": _prune_missing(htf_features),
            "recent_ohlcv": await state.recent_candles(
                session, symbol, htf, htf_as_of, n=20
            ),
        }

    # Episodic memory: surface the most-similar prior post-mortems as non-binding context.
    prior_lessons: list[dict[str, Any]] = []
    if settings.memory_enabled:
        try:
            from ats.learning.fingerprint import build_fingerprint
            from ats.learning.retrieval import retrieve_relevant_learnings

            fp = build_fingerprint(feature_row, regime)
            prior_lessons = await retrieve_relevant_learnings(
                session, fp, k=settings.memory_top_k
            )
        except Exception as exc:  # noqa: BLE001 — memory is advisory, never block a plan
            log.warning("retrieve_learnings_failed", symbol=symbol, error=str(exc))

    planner_context = await build_planner_context(
        session,
        symbol,
        tf,
        feature_row,
        as_of=as_of,
        regime=regime,
        recent_candles=candles,
        higher_timeframes=higher_timeframes,
    )

    return {
        "as_of": as_of,
        "symbol": symbol,
        "timeframe": tf,
        "features": _prune_missing(feature_row),
        "regime": regime,
        "recent_ohlcv": candles,
        "higher_timeframes": higher_timeframes,
        "portfolio": {
            "equity_usd": settings.paper_equity_usd,
            "open_positions": positions,
        },
        "risk_limits": {
            "risk_per_trade_pct": settings.risk_per_trade_pct,
            "max_leverage": settings.max_leverage,
            "max_margin_pct_per_trade": settings.max_margin_pct_per_trade,
            "max_total_margin_pct": settings.max_total_margin_pct,
            "max_portfolio_risk_pct": settings.max_portfolio_risk_pct,
            "min_rr": settings.min_rr,
            "one_position_per_symbol": True,
            # Directions the runtime regime filter will allow. Propose ONLY these.
            "allowed_directions": allowed_directions,
        },
        "prior_lessons": prior_lessons,
        "planner_context": planner_context,
    }


async def _persist(
    session: AsyncSession,
    *,
    symbol: str,
    tf: str,
    as_of: datetime,
    plan_out: PlanOutput,
    regime_cell: str | None,
    run_id: str | None = None,
) -> tuple[Plan, list[Setup]]:
    # Supersede only this run's active plans so concurrent replays don't clobber each other.
    where = [Plan.symbol == symbol, Plan.status == "active"]
    if run_id is not None:
        where.append(Plan.run_id == run_id)
    await session.execute(update(Plan).where(*where).values(status="superseded"))

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
        run_id=run_id,
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
            hard_rules=_stripped_hard_rules(s),
            soft_rules=[r.model_dump() for r in s.soft_rules],
            invalidation_rules=[r.model_dump() for r in _stripped_invalidation_rules(s)],
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
    run_id: str | None = None,
) -> PlanResult:
    """Run one create_plan cycle. Returns the persisted plan (or None on LLM failure)."""
    feature_row = await state.latest_feature_row(session, symbol, tf, as_of=as_of)
    if feature_row is None:
        raise ValueError(f"no features for {symbol} {tf} (run `ats process backfill` first)")
    effective_as_of = as_of or feature_row["open_time"]

    envelope = await build_envelope(session, symbol, tf, feature_row, as_of=effective_as_of, run_id=run_id)
    plan_out, llm = await client.create_plan(envelope, symbol=symbol)

    plan: Plan | None = None
    setups: list[Setup] = []
    if llm.parse_ok and plan_out is not None:
        available = set((envelope.get("features") or {}).keys()) | {"price"}
        _warn_unknown_features(plan_out, available, symbol=symbol)
        regime_cell = (envelope.get("regime") or {}).get("regime_cell")
        plan, setups = await _persist(
            session,
            symbol=symbol,
            tf=tf,
            as_of=effective_as_of,
            plan_out=plan_out,
            regime_cell=regime_cell,
            run_id=run_id,
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
