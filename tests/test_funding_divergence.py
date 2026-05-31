"""Tests for cross-venue funding divergence pure core."""

from __future__ import annotations

import pytest

from ats.processing.xvenue import funding_divergence_at


def test_zero_peers_returns_none() -> None:
    """0 non-null peers → None, peer_count=0."""
    div, count = funding_divergence_at(0.001, [None, None, None])
    assert div is None
    assert count == 0


def test_one_peer_returns_none() -> None:
    """1 non-null peer → None, peer_count=1 (requires ≥2)."""
    div, count = funding_divergence_at(0.001, [0.0005, None, None])
    assert div is None
    assert count == 1


def test_two_peers_computes_divergence() -> None:
    """2 non-null peers → divergence computed."""
    div, count = funding_divergence_at(0.001, [0.0005, 0.0003, None])
    assert count == 2
    assert div is not None
    # median([0.0005, 0.0003]) = 0.0004; div = 0.001 - 0.0004 = 0.0006
    assert div == pytest.approx(0.0006, abs=1e-9)


def test_three_peers_computes_divergence() -> None:
    """3 non-null peers → divergence computed with median of all three."""
    div, count = funding_divergence_at(0.002, [0.001, 0.0015, 0.0012])
    assert count == 3
    assert div is not None
    # median([0.001, 0.0012, 0.0015]) = 0.0012; div = 0.002 - 0.0012 = 0.0008
    assert div == pytest.approx(0.0008, abs=1e-9)


def test_binance_none_returns_none() -> None:
    """binance_rate=None → None regardless of peers."""
    div, count = funding_divergence_at(None, [0.001, 0.002, 0.003])
    assert div is None


def test_negative_divergence() -> None:
    """Binance rate below peer median → negative divergence."""
    div, count = funding_divergence_at(0.0005, [0.001, 0.0015, 0.002])
    assert count == 3
    assert div is not None
    # median = 0.0015; div = 0.0005 - 0.0015 = -0.001
    assert div == pytest.approx(-0.001, abs=1e-9)


def test_exactly_two_peers_passes() -> None:
    """Exactly 2 peers (threshold is >= 2) → should compute."""
    div, count = funding_divergence_at(0.003, [0.002, 0.001, None])
    assert count == 2
    assert div is not None
