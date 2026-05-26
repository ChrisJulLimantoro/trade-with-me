"""Test M1 universe loading."""

from __future__ import annotations

from ats.ingestion.universe import load_m1_universe


def test_universe_has_5_symbols() -> None:
    symbols = load_m1_universe()
    assert len(symbols) >= 5


def test_universe_includes_btcusdt() -> None:
    symbols = load_m1_universe()
    assert "BTCUSDT" in symbols


def test_universe_all_strings() -> None:
    symbols = load_m1_universe()
    assert all(isinstance(s, str) for s in symbols)
