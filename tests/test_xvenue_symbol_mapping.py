"""Test xvenue symbol mapping from seeds."""

from __future__ import annotations

from ats.ingestion.universe import load_xvenue_mapping


def test_btcusdt_okx_mapping() -> None:
    mapping = load_xvenue_mapping()
    assert mapping["BTCUSDT"]["okx"] == "BTC-USDT-SWAP"


def test_btcusdt_hyperliquid_mapping() -> None:
    mapping = load_xvenue_mapping()
    assert mapping["BTCUSDT"]["hyperliquid"] == "BTC"


def test_missing_symbol_returns_none() -> None:
    mapping = load_xvenue_mapping()
    missing = mapping.get("FAKEUSDT")
    assert missing is None


def test_all_m1_symbols_have_mapping() -> None:
    from ats.ingestion.universe import load_m1_universe

    mapping = load_xvenue_mapping()
    universe = load_m1_universe()
    for sym in universe:
        assert sym in mapping, f"{sym} missing from xvenue mapping"
        for venue in ("bybit", "okx", "hyperliquid"):
            assert venue in mapping[sym], f"{sym} missing {venue} mapping"
