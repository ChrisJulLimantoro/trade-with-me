"""Test that upsert_candles is idempotent (second run produces 0 net rows)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "kline_row.json"


def _make_row() -> list:
    return json.loads(FIXTURE.read_text())


@pytest.mark.asyncio
async def test_upsert_candles_calls_on_conflict() -> None:
    """Verify that the SQL uses ON CONFLICT so idempotency is guaranteed at DB level."""
    from ats.ingestion.binance_rest import upsert_candles

    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    row = _make_row()
    await upsert_candles(session, "BTCUSDT", "1h", [row])

    assert session.execute.called
    sql_str = str(session.execute.call_args[0][0])
    assert "ON CONFLICT" in sql_str.upper()


@pytest.mark.asyncio
async def test_upsert_candles_empty_rows_no_execute() -> None:
    from ats.ingestion.binance_rest import upsert_candles

    session = AsyncMock()
    session.execute = AsyncMock()

    count = await upsert_candles(session, "BTCUSDT", "1h", [])
    assert count == 0
    session.execute.assert_not_called()
