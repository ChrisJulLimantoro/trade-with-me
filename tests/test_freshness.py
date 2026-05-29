"""Test freshness status computation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ats.ingestion.freshness import BUDGETS, compute_status


def _ago(td: timedelta) -> datetime:
    return datetime.now(tz=UTC) - td


def test_ok_within_budget() -> None:
    budget = BUDGETS["kline_15m"]
    assert compute_status(_ago(timedelta(minutes=5)), budget) == "ok"


def test_stale_between_budget_and_2x() -> None:
    budget = BUDGETS["kline_15m"]  # 16 min
    assert compute_status(_ago(timedelta(minutes=20)), budget) == "stale"


def test_missing_beyond_2x() -> None:
    budget = BUDGETS["kline_15m"]  # 16 min → 2x = 32 min
    assert compute_status(_ago(timedelta(minutes=40)), budget) == "missing"


def test_missing_when_none() -> None:
    assert compute_status(None, BUDGETS["kline_15m"]) == "missing"


def test_funding_budget() -> None:
    budget = BUDGETS["funding"]  # 8h30m
    assert compute_status(_ago(timedelta(hours=8)), budget) == "ok"
    assert compute_status(_ago(timedelta(hours=9)), budget) == "stale"
    assert compute_status(_ago(timedelta(hours=18)), budget) == "missing"
