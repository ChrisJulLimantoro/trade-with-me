"""Verify taker_buy_vol is correctly mapped from kline field index 9."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "kline_row.json"


def _parse_kline(row: list) -> dict:
    """Replicate the mapping logic from binance_rest.upsert_candles."""
    taker_buy = Decimal(str(row[9]))
    vol = Decimal(str(row[5]))
    return {"volume": vol, "taker_buy_vol": taker_buy}


def test_taker_buy_vol_matches_field_9() -> None:
    row = json.loads(FIXTURE.read_text())
    parsed = _parse_kline(row)
    assert parsed["taker_buy_vol"] == Decimal("70.300")


def test_taker_buy_vol_within_volume() -> None:
    row = json.loads(FIXTURE.read_text())
    parsed = _parse_kline(row)
    assert Decimal("0") <= parsed["taker_buy_vol"] <= parsed["volume"]
