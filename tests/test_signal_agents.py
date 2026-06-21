"""Per-agent golden behavior for the deterministic signal engine (spec 04 §Validation)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ats.agents.base import AgentInput
from ats.agents.basis import BasisAgent
from ats.agents.cross_venue import CrossVenueAgent
from ats.agents.cvd import CvdAgent
from ats.agents.funding import FundingAgent
from ats.agents.liquidity import LiquidityAgent
from ats.agents.momentum import MomentumAgent
from ats.agents.price_action import PriceActionAgent
from ats.agents.structure import StructureAgent

T0 = datetime(2026, 1, 1, 12, 0)


def _candle(h: float, lo: float, c: float, v: float = 1000.0) -> dict[str, Any]:
    return {"open": c, "high": h, "low": lo, "close": c, "volume": v}


def _ai(
    features: dict[str, Any],
    candles: list[dict] | None = None,
    *,
    regime: dict | None = None,
    htf: dict | None = None,
) -> AgentInput:
    return AgentInput(
        symbol="BTCUSDT",
        cycle_ts=T0,
        timeframe_primary="15m",
        features=features,
        recent_ohlcv=candles or [],
        regime=regime or {},
        higher_timeframes=htf or {},
    )


# --- Structure ---------------------------------------------------------------------


def test_structure_breakout_is_long() -> None:
    bars = [_candle(100, 98, 99) for _ in range(20)] + [_candle(106, 104, 105, v=2000)]
    s = StructureAgent().run(_ai({"close": 105, "atr_14": 2.0, "vol_zscore_20": 2.0}, bars))
    assert s.direction == "long"
    assert s.score > 0.7


def test_structure_no_breakout_without_volume() -> None:
    bars = [_candle(100, 98, 99) for _ in range(20)] + [_candle(106, 104, 105, v=900)]
    s = StructureAgent().run(_ai({"close": 105, "atr_14": 2.0, "vol_zscore_20": 0.5}, bars))
    assert s.direction == "neutral"
    assert s.score == 0.0


# --- Momentum ----------------------------------------------------------------------


def test_momentum_long_short_neutral() -> None:
    long = MomentumAgent().run(_ai({"rsi_14": 60, "macd_hist": 0.5, "momentum_composite": 0.8}))
    short = MomentumAgent().run(_ai({"rsi_14": 40, "macd_hist": -0.5, "momentum_composite": 0.2}))
    flat = MomentumAgent().run(_ai({"rsi_14": 50, "macd_hist": 0.1, "momentum_composite": 0.5}))
    assert (long.direction, short.direction, flat.direction) == ("long", "short", "neutral")
    assert long.score > 0.6 and short.score > 0.6 and flat.score == 0.0


# --- Funding -----------------------------------------------------------------------


def test_funding_fades_extreme_and_abstains_otherwise() -> None:
    short = FundingAgent().run(_ai({"funding_z_30d": 2.5}))
    long = FundingAgent().run(_ai({"funding_z_30d": -2.4}))
    flat = FundingAgent().run(_ai({"funding_z_30d": 1.0}))
    missing = FundingAgent().run(_ai({}))
    assert short.direction == "short" and long.direction == "long"
    # New formula: score ramps from z=1.0, full at z=3.0 → (2.5-1.0)/2.0 = 0.75
    assert abs(short.score - 0.75) < 1e-5
    assert flat.direction == "neutral"
    assert missing.direction == "neutral" and missing.metadata["reason"] == "no_funding_z"


# --- CrossVenueFlow ----------------------------------------------------------------


def test_cross_venue_abstains_below_two_peers() -> None:
    one = CrossVenueAgent().run(_ai({"funding_peer_count": 1, "funding_divergence_z_30d": 3.0}))
    assert one.score == 0.0 and one.direction == "neutral"
    assert one.metadata["reason"] == "insufficient_peers"


def test_cross_venue_fades_divergence() -> None:
    s = CrossVenueAgent().run(_ai({"funding_peer_count": 3, "funding_divergence_z_30d": 2.0}))
    assert s.direction == "short"
    assert abs(s.score - 2.0 / 3.0) < 1e-5


# --- Basis -------------------------------------------------------------------------


def test_basis_abstains_on_null_and_fades_extreme() -> None:
    null = BasisAgent().run(_ai({}))
    rich = BasisAgent().run(_ai({"basis_z_30d": 2.5}))
    assert null.score == 0.0 and null.metadata["reason"] == "no_basis"
    assert rich.direction == "short"


# --- CVD ---------------------------------------------------------------------------


def test_cvd_abstains_on_null_cvd() -> None:
    # No CVD data at all (neither cvd_slope_10 nor pr_cvd_divergence) → abstain
    s = CvdAgent().run(_ai({}, [_candle(10, 9, 9), _candle(11, 10, 10)]))
    assert s.score == 0.0 and s.metadata["reason"] == "no_cvd"


def test_cvd_bearish_divergence_on_new_high() -> None:
    bars = [_candle(10 + i, 9 + i, 9 + i) for i in range(30)]  # ascending → last is new high
    s = CvdAgent().run(_ai({"cvd_30": 100.0, "pr_cvd_divergence": 0.7, "close": 38}, bars))
    assert s.direction == "short"
    assert s.metadata["divergence_type"] == "bearish"
    assert abs(s.score - 0.7) < 1e-9


# --- PriceAction (FVG) -------------------------------------------------------------


def test_price_action_bullish_fvg_emits_zone() -> None:
    bars = [_candle(100, 98, 99), _candle(105, 99, 104), _candle(108, 102, 107)]
    s = PriceActionAgent().run(_ai({"atr_14": 2.0}, bars))
    assert s.direction == "long"
    assert s.metadata["fvg_zone"] == [100.0, 102.0]
    assert s.score > 0.6


def test_price_action_no_gap_abstains() -> None:
    bars = [_candle(100, 98, 99) for _ in range(5)]
    s = PriceActionAgent().run(_ai({"atr_14": 2.0}, bars))
    assert s.direction == "neutral" and s.metadata["reason"] == "no_fvg"


def test_price_action_mitigated_gap_abstains() -> None:
    # Bullish gap [100, 102] but a later candle closes back inside it → mitigated.
    bars = [
        _candle(100, 98, 99),
        _candle(105, 99, 104),
        _candle(108, 102, 107),
        _candle(103, 100, 101),  # closes at 101, inside the [100, 102] gap
    ]
    s = PriceActionAgent().run(_ai({"atr_14": 2.0}, bars))
    assert s.direction == "neutral" and s.metadata["reason"] == "fvg_mitigated"


# --- Liquidity proxy ---------------------------------------------------------------


def test_liquidity_proxy_is_honest_and_bounded() -> None:
    bars = [_candle(100 + (i % 3), 96 + (i % 3), 98 + (i % 3), v=1000 + i) for i in range(30)]
    s = LiquidityAgent().run(_ai({"close": 98, "atr_14": 2.0}, bars))
    assert s.metadata.get("proxy") is True
    assert 0.0 <= s.score <= 0.6
