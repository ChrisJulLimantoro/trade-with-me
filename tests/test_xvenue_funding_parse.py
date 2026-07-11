"""Test cross-venue funding parsing — alignment to 8h UTC boundaries."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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
    return dt.hour in _8H_BOUNDARIES and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0


def test_align_8h_snaps_correctly() -> None:
    dt = datetime(2024, 1, 1, 10, 30, 0, tzinfo=UTC)
    aligned = _align_8h(dt)
    assert aligned.hour == 8
    assert aligned.minute == 0


def test_align_8h_already_aligned() -> None:
    dt = datetime(2024, 1, 1, 16, 0, 0, tzinfo=UTC)
    assert _align_8h(dt) == dt


def test_align_8h_strips_millisecond_offset() -> None:
    """Binance fundingTime often lands a few ms past the boundary — must snap clean."""
    dt = datetime(2026, 7, 10, 8, 0, 0, 5000, tzinfo=UTC)
    assert _align_8h(dt) == datetime(2026, 7, 10, 8, 0, 0, tzinfo=UTC)


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

    # API requires startTime — omitting it is what caused the live 422s.
    req = httpx_mock.get_request()
    body = json.loads(req.content.decode())
    assert body["type"] == "fundingHistory"
    assert body["coin"] == "BTC"
    assert "startTime" in body
    assert isinstance(body["startTime"], int)


@pytest.mark.asyncio
async def test_hyperliquid_funding_paginates(httpx_mock) -> None:
    """HL caps at 500 rows; we page with last.time+1 until a short page."""
    t0 = int(datetime(2026, 5, 1, tzinfo=UTC).timestamp() * 1000)
    page1 = [
        {"time": t0 + i * 3_600_000, "fundingRate": "0.0001"}
        for i in range(500)
    ]
    page2 = [
        {"time": t0 + (500 + i) * 3_600_000, "fundingRate": "0.0002"}
        for i in range(10)
    ]
    httpx_mock.add_response(json=page1)
    httpx_mock.add_response(json=page2)

    import httpx
    async with httpx.AsyncClient() as client:
        rows = await fetch_hyperliquid_funding(
            client,
            "SOL",
            start_time_ms=t0,
            end_time_ms=t0 + 600 * 3_600_000,
        )

    assert len(httpx_mock.get_requests()) == 2
    second = json.loads(httpx_mock.get_requests()[1].content.decode())
    assert second["startTime"] == page1[-1]["time"] + 1
    # Hourly stamps collapse to 8h buckets; 510 hours → fewer boundaries.
    assert len(rows) < 510
    assert all(_is_8h_boundary(r["funding_time"]) for r in rows)


@pytest.mark.asyncio
async def test_hyperliquid_hourly_collapses_to_8h_last_wins(httpx_mock) -> None:
    """Several hourly rates in one 8h bucket → one row, last rate wins."""
    base = datetime(2026, 7, 10, 0, 0, tzinfo=UTC)
    items = [
        {
            "time": int((base + timedelta(hours=h)).timestamp() * 1000),
            "fundingRate": str(0.0001 * (h + 1)),
        }
        for h in range(8)  # 00..07 → all snap to 00:00
    ]
    httpx_mock.add_response(json=items)
    import httpx
    async with httpx.AsyncClient() as client:
        rows = await fetch_hyperliquid_funding(
            client,
            "SOL",
            start_time_ms=int(base.timestamp() * 1000),
            end_time_ms=int((base + timedelta(hours=8)).timestamp() * 1000),
        )
    assert len(rows) == 1
    assert rows[0]["funding_time"] == base
    assert float(rows[0]["rate"]) == pytest.approx(0.0008)
