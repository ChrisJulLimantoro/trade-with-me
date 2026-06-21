"""DB read helpers shared by planning + the engine loop.

Reads the spec-01/02 tables (candles, features, regimes) and the new trading tables
(plans, setups, paper_trades). Feature rows are returned as plain dicts joined with
the bar's OHLC and an injected ``price`` (= close), ready for the rule engine and for
JSON envelopes. Decimals are coerced to float so everything is JSON-serializable.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ats.db.models import Plan, Setup

# features.* joined with the bar's candle. Selecting f.* keeps us in sync with the
# schema; the candle columns add price context the rule engine needs.
_FEATURE_SQL = """
    SELECT f.*, c.open, c.high, c.low, c.close, c.volume, c.taker_buy_vol
    FROM features f
    JOIN candles c
      ON c.symbol = f.symbol AND c.timeframe = f.timeframe AND c.open_time = f.open_time
    WHERE f.symbol = :symbol AND f.timeframe = :tf
"""


def _coerce(v: Any) -> Any:
    return float(v) if isinstance(v, Decimal) else v


def _feature_dict(mapping: Any) -> dict[str, Any]:
    d = {k: _coerce(v) for k, v in dict(mapping).items()}
    d["price"] = d.get("close")
    return d


async def latest_feature_row(
    session: AsyncSession, symbol: str, tf: str, *, as_of: datetime | None = None
) -> dict[str, Any] | None:
    sql = _FEATURE_SQL + (" AND f.open_time <= :as_of" if as_of else "")
    sql += " ORDER BY f.open_time DESC LIMIT 1"
    params: dict[str, Any] = {"symbol": symbol, "tf": tf}
    if as_of:
        params["as_of"] = as_of
    row = (await session.execute(text(sql), params)).mappings().first()
    return _feature_dict(row) if row else None


async def feature_rows_since(
    session: AsyncSession,
    symbol: str,
    tf: str,
    since: datetime,
    *,
    until: datetime | None = None,
) -> list[dict[str, Any]]:
    """Ordered feature+candle rows for replay (oldest → newest).

    Timing note: each row is labelled with its bar's ``open_time`` (= T). The features
    (RSI, EMA, MACD …) are computed from that bar's *close* at T+tf, so a decision made on
    a row uses indicators that are only known once the bar has closed.

    "close" entry mode fills at that same close — executable, because acting at the close
    you just decided on is something a live system can do. "wick_limit" mode must NOT fill
    on the deciding bar: doing so books a fill at an intrabar wick that the bar has already
    retraced from by its close, while the authorization to fill came from that same close —
    a price unreachable live. That is why wick_limit ARMS a resting limit on the deciding
    bar and only fills on a *later* bar that trades through it (orchestrator Phase 1).
    """
    sql = _FEATURE_SQL + " AND f.open_time >= :since"
    params: dict[str, Any] = {"symbol": symbol, "tf": tf, "since": since}
    if until:
        sql += " AND f.open_time <= :until"
        params["until"] = until
    sql += " ORDER BY f.open_time ASC"
    rows = (await session.execute(text(sql), params)).mappings().all()
    return [_feature_dict(r) for r in rows]


async def recent_feature_rows(
    session: AsyncSession,
    symbol: str,
    tf: str,
    before_ts: datetime,
    *,
    n: int = 50,
) -> list[dict[str, Any]]:
    """Recent feature+candle rows up to ``before_ts`` ordered oldest → newest."""
    sql = _FEATURE_SQL + " AND f.open_time <= :before ORDER BY f.open_time DESC LIMIT :n"
    rows = (
        await session.execute(
            text(sql), {"symbol": symbol, "tf": tf, "before": before_ts, "n": n}
        )
    ).mappings().all()
    return [_feature_dict(r) for r in reversed(rows)]


async def recent_candles(
    session: AsyncSession, symbol: str, tf: str, before_ts: datetime, *, n: int = 50
) -> list[dict[str, Any]]:
    sql = text(
        """
        SELECT open_time, open, high, low, close, volume, taker_buy_vol
        FROM candles
        WHERE symbol = :symbol AND timeframe = :tf AND open_time <= :before
        ORDER BY open_time DESC LIMIT :n
        """
    )
    rows = (
        await session.execute(
            sql, {"symbol": symbol, "tf": tf, "before": before_ts, "n": n}
        )
    ).mappings().all()
    out = [{k: _coerce(v) for k, v in dict(r).items()} for r in reversed(rows)]
    return out


async def candles_between(
    session: AsyncSession, symbol: str, tf: str, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    sql = text(
        """
        SELECT open_time, open, high, low, close, volume, taker_buy_vol
        FROM candles
        WHERE symbol = :symbol AND timeframe = :tf
          AND open_time > :start AND open_time <= :end
        ORDER BY open_time ASC
        """
    )
    rows = (
        await session.execute(
            sql, {"symbol": symbol, "tf": tf, "start": start, "end": end}
        )
    ).mappings().all()
    return [{k: _coerce(v) for k, v in dict(r).items()} for r in rows]


async def latest_regime(
    session: AsyncSession, *, before_ts: datetime | None = None
) -> dict[str, Any] | None:
    sql = "SELECT * FROM regimes"
    params: dict[str, Any] = {}
    if before_ts:
        sql += " WHERE ts <= :before"
        params["before"] = before_ts
    sql += " ORDER BY ts DESC LIMIT 1"
    row = (await session.execute(text(sql), params)).mappings().first()
    return {k: _coerce(v) for k, v in dict(row).items()} if row else None


async def open_positions(
    session: AsyncSession, *, symbol: str | None = None, run_id: str | None = None
) -> list[dict[str, Any]]:
    sql = (
        "SELECT trade_id, symbol, direction, size_pct, leverage, margin_usd, "
        "notional_usd, risk_usd, entry_price "
        "FROM paper_trades WHERE status = 'open'"
    )
    params: dict[str, Any] = {}
    if symbol:
        sql += " AND symbol = :symbol"
        params["symbol"] = symbol
    if run_id is not None:
        sql += " AND run_id = :run_id"
        params["run_id"] = run_id
    rows = (await session.execute(text(sql), params)).mappings().all()
    return [{k: _coerce(v) for k, v in dict(r).items()} for r in rows]


async def active_plan(session: AsyncSession, symbol: str, *, run_id: str | None = None) -> Plan | None:
    stmt = (
        select(Plan)
        .where(Plan.symbol == symbol, Plan.status == "active")
        .order_by(Plan.created_at.desc())
        .limit(1)
    )
    if run_id is not None:
        stmt = stmt.where(Plan.run_id == run_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def plan_setups(session: AsyncSession, plan_id: uuid.UUID) -> list[Setup]:
    stmt = select(Setup).where(Setup.plan_id == plan_id)
    return list((await session.execute(stmt)).scalars().all())


async def supersede_active_plan(session: AsyncSession, symbol: str, *, run_id: str | None = None) -> int:
    """Mark the symbol's active plan superseded so the next bar builds a fresh one.

    Used after a trade closes: the thesis that motivated the closed trade is stale, so we
    discard it rather than acting on it again. Returns the number of plans superseded.
    """
    sql = "UPDATE plans SET status = 'superseded' WHERE symbol = :symbol AND status = 'active'"
    params: dict[str, Any] = {"symbol": symbol}
    if run_id is not None:
        sql += " AND run_id = :run_id"
        params["run_id"] = run_id
    result = await session.execute(text(sql), params)
    await session.flush()
    return result.rowcount or 0
