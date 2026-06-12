"""Retrieve the most-similar prior learnings for a setup (cosine over the fingerprint)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ats.learning.fingerprint import to_vector_literal


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


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


async def retrieve_memory_summary(
    session: AsyncSession,
    fingerprint: list[float],
    *,
    k: int = 50,
    min_confident_count: int = 10,
) -> dict[str, Any]:
    """Aggregate nearby learning rows into a compact planner-facing memory signal."""
    fp = to_vector_literal(fingerprint)
    rows = (
        await session.execute(
            text(
                "SELECT category, hypothesis, proposed_adjustment, outcome, pnl_pct, "
                "(fingerprint <=> CAST(:fp AS vector)) AS distance "
                "FROM learnings "
                "ORDER BY fingerprint <=> CAST(:fp AS vector) LIMIT :k"
            ),
            {"fp": fp, "k": k},
        )
    ).mappings().all()

    count = len(rows)
    pnls = [float(r["pnl_pct"]) for r in rows if r["pnl_pct"] is not None]
    wins = [
        r
        for r in rows
        if str(r.get("outcome") or "").lower() == "win"
        or (r["pnl_pct"] is not None and float(r["pnl_pct"]) > 0)
    ]
    losses = [
        r
        for r in rows
        if str(r.get("outcome") or "").lower() == "loss"
        or (r["pnl_pct"] is not None and float(r["pnl_pct"]) < 0)
    ]

    top_failure: str | None = None
    lesson: str | None = None
    if count >= min_confident_count and losses:
        category_counts: dict[str, int] = {}
        category_pnl: dict[str, list[float]] = {}
        for r in losses:
            cat = str(r["category"])
            category_counts[cat] = category_counts.get(cat, 0) + 1
            if r["pnl_pct"] is not None:
                category_pnl.setdefault(cat, []).append(float(r["pnl_pct"]))
        top_failure = max(
            category_counts,
            key=lambda c: (category_counts[c], -(_avg(category_pnl.get(c, [])) or 0.0)),
        )
        for r in losses:
            if r["category"] == top_failure:
                lesson = r["proposed_adjustment"] or r["hypothesis"]
                break

    return {
        "similar_count": count,
        "win_rate": (len(wins) / count) if count else None,
        "avg_pnl_pct": _avg(pnls),
        "expectancy_pct": _avg(pnls),
        "top_failure": top_failure,
        "lesson": lesson,
        "confidence": "normal" if count >= min_confident_count else "low",
    }
