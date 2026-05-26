from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

BUDGETS: dict[str, timedelta] = {
    "kline_15m": timedelta(minutes=16),
    "kline_1h": timedelta(minutes=61),
    "kline_4h": timedelta(hours=4, minutes=1),
    "funding": timedelta(hours=8, minutes=30),
    "funding_bybit": timedelta(hours=8, minutes=30),
    "funding_okx": timedelta(hours=8, minutes=30),
    "funding_hyperliquid": timedelta(hours=8, minutes=30),
    "open_int": timedelta(minutes=6),
    "basis": timedelta(minutes=6),
    "mark_price": timedelta(seconds=90),
    "media_x": timedelta(minutes=65),
    "media_rss": timedelta(minutes=35),
}


def compute_status(
    last_seen: datetime | None, budget: timedelta
) -> str:
    if last_seen is None:
        return "missing"
    now = datetime.now(tz=UTC)
    age = now - last_seen
    if age <= budget:
        return "ok"
    if age <= budget * 2:
        return "stale"
    return "missing"


async def upsert_heartbeat(
    session: AsyncSession,
    data_class: str,
    status: str,
    detail: str | None = None,
) -> None:
    now = datetime.now(tz=UTC)
    await session.execute(
        text("""
            INSERT INTO heartbeats (data_class, last_seen_at, status, detail, updated_at)
            VALUES (:data_class, :now, :status, :detail, :now)
            ON CONFLICT (data_class) DO UPDATE SET
              last_seen_at=EXCLUDED.last_seen_at,
              status=EXCLUDED.status,
              detail=EXCLUDED.detail,
              updated_at=EXCLUDED.updated_at
        """),
        {"data_class": data_class, "now": now, "status": status, "detail": detail},
    )
    await session.commit()


async def get_row_counts(session: AsyncSession) -> dict[str, int]:
    counts: dict[str, int] = {}
    tables = [
        "candles", "funding_rates", "open_interest",
        "funding_rates_xvenue", "basis", "media_items",
    ]
    for table in tables:
        result = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
        counts[table] = result.scalar() or 0
    return counts


async def check_freshness(session: AsyncSession) -> list[dict[str, Any]]:
    result = await session.execute(
        text("SELECT data_class, last_seen_at, status, detail FROM heartbeats")
    )
    rows = result.fetchall()
    hb_map = {r[0]: r for r in rows}

    output = []
    for dc, budget in BUDGETS.items():
        hb = hb_map.get(dc)
        if hb is None:
            status = "missing"
            last_seen = None
            detail = None
        else:
            last_seen = hb[1]
            status = "disabled" if hb[2] == "disabled" else compute_status(last_seen, budget)
            detail = hb[3]
        output.append({
            "data_class": dc, "status": status, "last_seen": last_seen, "detail": detail,
        })
    return output
