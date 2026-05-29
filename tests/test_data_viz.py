"""Tests for the data-viz sparkline helper and Tier-3 freshness handling."""

from __future__ import annotations

from ats.cli_commands.data import _SPARK_BLOCKS, _sparkline
from ats.ingestion.freshness import TIER3_ONLY


def test_sparkline_empty() -> None:
    assert _sparkline([]) == ""


def test_sparkline_constant_series_is_lowest_block() -> None:
    assert _sparkline([5.0, 5.0, 5.0]) == _SPARK_BLOCKS[0] * 3


def test_sparkline_monotonic_maps_full_range() -> None:
    # 8 evenly spaced values over the 8-block alphabet → one of each, in order
    series = [float(i) for i in range(8)]
    assert _sparkline(series) == _SPARK_BLOCKS


def test_sparkline_endpoints() -> None:
    spark = _sparkline([1.0, 10.0, 100.0])
    assert spark[0] == _SPARK_BLOCKS[0]   # min → lowest
    assert spark[-1] == _SPARK_BLOCKS[-1]  # max → highest


def test_mark_price_is_tier3_only() -> None:
    # Drives the n/a (not missing) status in Tier 1 — see ats data status.
    assert "mark_price" in TIER3_ONLY
