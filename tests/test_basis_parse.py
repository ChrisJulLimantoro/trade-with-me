"""Test basis (premium_index) computation from premiumIndex fixture."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "premium_index.json"


def _compute_premium_index(mark: Decimal, index: Decimal) -> Decimal:
    return (mark - index) / index


def test_premium_index_computation() -> None:
    row = json.loads(FIXTURE.read_text())
    mark = Decimal(str(row["markPrice"]))
    index = Decimal(str(row["indexPrice"]))
    computed = _compute_premium_index(mark, index)

    # Verify manual calculation: (68200.50 - 68150.00) / 68150.00
    expected = (Decimal("68200.50") - Decimal("68150.00")) / Decimal("68150.00")
    assert abs(computed - expected) < Decimal("1e-9")


def test_premium_index_within_sane_range() -> None:
    row = json.loads(FIXTURE.read_text())
    mark = Decimal(str(row["markPrice"]))
    index = Decimal(str(row["indexPrice"]))
    computed = _compute_premium_index(mark, index)
    # Should be a small percentage, not a huge number
    assert abs(computed) < Decimal("0.05")
