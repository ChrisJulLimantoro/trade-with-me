"""Tests for paper executor PnL accounting helpers."""

from __future__ import annotations

import pytest

from ats.execution.executor import margin_close_values


def test_margin_close_values_profit() -> None:
    pnl_usd, pnl_pct = margin_close_values(
        notional_usd=600.0, margin_usd=200.0, notional_pnl_pct=0.01
    )
    assert pnl_usd == pytest.approx(6.0)
    assert pnl_pct == pytest.approx(0.03)


def test_margin_close_values_loss() -> None:
    pnl_usd, pnl_pct = margin_close_values(
        notional_usd=600.0, margin_usd=200.0, notional_pnl_pct=-0.01
    )
    assert pnl_usd == pytest.approx(-6.0)
    assert pnl_pct == pytest.approx(-0.03)
