"""Test cross-venue funding parsing — alignment to 8h UTC boundaries."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ats.ingestion.xvenue_funding import (
    _align_8h,
    fetch_bybit_funding,
    fetch_hyperliquid_funding,
    fetch_okx_funding,
)

FIXTURES = Path(__file__).parent / "fixtures"

_8H_BOUNDARIES = {0, 8, 16}


def _is_8h_boundary(dt: datetime) -> bool:
    return dt.hour in _8H_BOUNDARIES and dt.minute == 0 and dt.second == 0


def test_align_8h_snaps_correctly() -> None:
    dt = datetime(2024, 1, 1, 10, 30, 0, tzinfo=UTC)
    aligned = _align_8h(dt)
    assert aligned.hour == 8
    assert aligned.minute == 0


def test_align_8h_already_aligned() -> None:
    dt = datetime(2024, 1, 1, 16, 0, 0, tzinfo=UTC)
    assert _align_8h(dt) == dt


@pytest.mark.asyncio
async def test_bybit_funding_parse(httpx_mock) -> None:
    data = json.loads((FIXTURES / "bybit_funding.json").read_text())
    httpx_mock.add_response(json=data)
    import httpx
    async with httpx.AsyncClient() as client:
        rows = await fetch_bybit_funding(client, "BTCUSDT")
    assert len(rows) == 2
    for row in rows:
        assert _is_8h_boundary(row["funding_time"])
        assert isinstance(float(row["rate"]), float)


@pytest.mark.asyncio
async def test_okx_funding_parse(httpx_mock) -> None:
    data = json.loads((FIXTURES / "okx_funding.json").read_text())
    httpx_mock.add_response(json=data)
    import httpx
    async with httpx.AsyncClient() as client:
        rows = await fetch_okx_funding(client, "BTC-USDT-SWAP")
    assert len(rows) == 2
    for row in rows:
        assert _is_8h_boundary(row["funding_time"])


@pytest.mark.asyncio
async def test_hyperliquid_funding_parse(httpx_mock) -> None:
    data = json.loads((FIXTURES / "hyperliquid_funding.json").read_text())
    httpx_mock.add_response(json=data)
    import httpx
    async with httpx.AsyncClient() as client:
        rows = await fetch_hyperliquid_funding(client, "BTC")
    assert len(rows) == 2
    for row in rows:
        assert _is_8h_boundary(row["funding_time"])
