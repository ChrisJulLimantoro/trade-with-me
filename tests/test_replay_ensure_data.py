"""`_ensure_data` (replay auto-backfill) triggers backfill only when the window is uncovered."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

FROM_DT = datetime(2026, 5, 1, tzinfo=UTC)
TO_DT = datetime(2026, 5, 15, tzinfo=UTC)


def _session_with_coverage(mn: datetime | None, mx: datetime | None) -> AsyncMock:
    result = MagicMock()
    result.mappings.return_value.first.return_value = {"mn": mn, "mx": mx}
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_skips_backfill_when_window_covered(monkeypatch) -> None:
    from ats.cli_commands import engine

    ingest = AsyncMock()
    feat = AsyncMock()
    monkeypatch.setattr("ats.ingestion.backfill.run", ingest)
    monkeypatch.setattr("ats.processing.features.backfill", feat)

    # Features span a wider range than [from, to] → covered.
    session = _session_with_coverage(datetime(2026, 4, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC))
    await engine._ensure_data(session, "ETHUSDT", "15m", FROM_DT, TO_DT)

    ingest.assert_not_called()
    feat.assert_not_called()


@pytest.mark.asyncio
async def test_triggers_backfill_when_uncovered(monkeypatch) -> None:
    from ats.cli_commands import engine

    ingest = AsyncMock()
    feat = AsyncMock()
    monkeypatch.setattr("ats.ingestion.backfill.run", ingest)
    monkeypatch.setattr("ats.processing.features.backfill", feat)

    # No features at all → uncovered. Non-BTC symbol skips the regime branch.
    session = _session_with_coverage(None, None)
    await engine._ensure_data(session, "ETHUSDT", "15m", FROM_DT, TO_DT)

    ingest.assert_awaited_once()
    feat.assert_awaited_once()
    # Klines fetched from 90d before from_dt through to_dt.
    assert ingest.await_args.kwargs["end"] == TO_DT
    assert ingest.await_args.kwargs["start"] < FROM_DT
    session.commit.assert_awaited()
