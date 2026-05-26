"""WS parsing tests — skipped in M1 (Tier 3 / M4 only)."""

import pytest


@pytest.mark.skip(reason="Binance WebSocket client is Tier 3 / M4 — not implemented in M1")
def test_ws_kline_dispatch() -> None:
    pass


@pytest.mark.skip(reason="Binance WebSocket client is Tier 3 / M4 — not implemented in M1")
def test_ws_liquidation_dispatch() -> None:
    pass
