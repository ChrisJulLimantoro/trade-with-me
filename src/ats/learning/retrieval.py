"""Retrieve the most-similar prior learnings for a setup (cosine over the fingerprint)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ats.learning.fingerprint import to_vector_literal


async def retrieve_relevant_learnings(
    session: AsyncSession,
    fingerprint: list[float],
    *,
    direction: str | None = None,
    k: int = 3,
    max_per_category: int = 1,
) -> list[dict[str, Any]]:
    """Top-k learnings nearest to ``fingerprint`` by cosine distance.

    ``direction`` optionally pre-filters (longs vs shorts); at plan time the direction is
    not yet decided, so it is left None. Returns plain dicts ready for a JSON envelope.

    ``max_per_category`` caps how many lessons from the same category can appear in the
    result (default 1). This prevents a single dominant category (e.g. "false_breakout")
    from monopolising all retrieved lessons and biasing the strategist in one direction.
    We fetch ``k * max(k, max_per_category * 3)`` candidates and then filter down.
    """
    fp = to_vector_literal(fingerprint)
    # Fetch more candidates than k so the diversity filter has material to work with.
    fetch_k = k * max(k, max_per_category * 3)
    sql = (
        "SELECT category, hypothesis, proposed_adjustment, outcome, pnl_pct, "
        "direction, regime_cell, (fingerprint <=> CAST(:fp AS vector)) AS distance "
        "FROM learnings"
    )
    params: dict[str, Any] = {"fp": fp, "k": fetch_k}
    if direction:
        sql += " WHERE direction = :direction"
        params["direction"] = direction
    sql += " ORDER BY fingerprint <=> CAST(:fp AS vector) LIMIT :k"
    rows = (await session.execute(text(sql), params)).mappings().all()

    out: list[dict[str, Any]] = []
    category_counts: dict[str, int] = {}
    for r in rows:
        if len(out) >= k:
            break
        cat = r["category"]
        if category_counts.get(cat, 0) >= max_per_category:
            continue
        pnl = r["pnl_pct"]
        out.append(
            {
                "category": cat,
                "hypothesis": r["hypothesis"],
                "proposed_adjustment": r["proposed_adjustment"],
                "outcome": r["outcome"],
                "pnl_pct": float(pnl) if pnl is not None else None,
                "direction": r["direction"],
                "regime_cell": r["regime_cell"],
            }
        )
        category_counts[cat] = category_counts.get(cat, 0) + 1
    return out
