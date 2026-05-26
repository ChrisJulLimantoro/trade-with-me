"""Mark price downsampling tests — skipped in M1 (Tier 3 / M4 only)."""

import pytest


@pytest.mark.skip(reason="Mark price downsampling is Tier 3 / M4 — not implemented in M1")
def test_60_samples_produce_1m_row() -> None:
    pass
