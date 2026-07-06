"""Tests for conviction-based sizing (i10 mechanism, default OFF).

Covers:
- ``conviction_size_multiplier`` math (linear on the confidence band, no-op when OFF
  or when ``confidence`` is mizssing/legacy).
- ``setup_dict`` round-trips the persisted confidence from ``setup_metadata``.
- the swing profile opts in with the documented bounds; every other profile keeps the
  mechanism byte-unchanged (default OFF → ``conviction_sizing_enabled=False``).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ats.config import Settings
from ats.engine.entries import conviction_size_multiplier, setup_dict
from ats.strategy_profiles import apply_profile


def test_off_returns_one_regardless_of_confidence() -> None:
    assert conviction_size_multiplier(0.95, enabled=False, size_min=0.7, size_max=1.4,
                                      conf_floor=0.0, fallback_floor=0.6) == 1.0
    # and regardless of where on the band
    assert conviction_size_multiplier(0.61, enabled=False, size_min=0.7, size_max=1.4,
                                      conf_floor=0.0, fallback_floor=0.6) == 1.0


def test_missing_confidence_is_noop() -> None:
    # legacy setups persisted before this field existed OR vetoed-signal plans (no
    # confidence stored). Must never gate-kill on missing data → 1.0.
    assert conviction_size_multiplier(None, enabled=True, size_min=0.7, size_max=1.4,
                                      conf_floor=0.0, fallback_floor=0.6) == 1.0


def test_floor_setup_gets_size_min() -> None:
    # confidence at the admission floor → frac=0 → returns size_min
    m = conviction_size_multiplier(0.6, enabled=True, size_min=0.7, size_max=1.4,
                                  conf_floor=0.0, fallback_floor=0.6)
    assert m == pytest.approx(0.7)


def test_floor_explicit_overrides_fallback() -> None:
    # explicit conviction_size_conf_floor wins over signal_min_confidence
    m = conviction_size_multiplier(0.65, enabled=True, size_min=0.7, size_max=1.4,
                                  conf_floor=0.65, fallback_floor=0.6)
    assert m == pytest.approx(0.7)


def test_peak_confidence_gets_size_max() -> None:
    m = conviction_size_multiplier(1.0, enabled=True, size_min=0.7, size_max=1.4,
                                   conf_floor=0.0, fallback_floor=0.6)
    assert m == pytest.approx(1.4)


def test_mid_confidence_is_linear() -> None:
    # confidence = 0.8 → frac = (0.8-0.6)/(1.0-0.6) = 0.5 → 0.7 + (1.4-0.7)*0.5 = 1.05
    m = conviction_size_multiplier(0.8, enabled=True, size_min=0.7, size_max=1.4,
                                    conf_floor=0.0, fallback_floor=0.6)
    assert m == pytest.approx(1.05)


def test_below_floor_clamps_to_size_min() -> None:
    # confidence below the floor (admission-allowable quirk) → clamp to 0 → size_min
    m = conviction_size_multiplier(0.45, enabled=True, size_min=0.7, size_max=1.4,
                                   conf_floor=0.0, fallback_floor=0.6)
    assert m == pytest.approx(0.7)


def test_floor_at_or_above_one_returns_size_max() -> None:
    # divide-by-zero avoidance: floor==1.0 → everything is "at the max"
    m = conviction_size_multiplier(0.9, enabled=True, size_min=0.7, size_max=1.4,
                                   conf_floor=1.0, fallback_floor=0.6)
    assert m == pytest.approx(1.4)


def test_default_off_in_baseline_and_scalper() -> None:
    # The mechanism must default OFF for every profile that doesn't opt in, so baseline
    # / scalper reputation semantics stay byte-identical (the spec's byte-unchanged contract).
    for name in ("baseline", "scalper"):
        target = Settings(_env_file=None)
        apply_profile(name, target)
        assert target.risk.conviction_sizing_enabled is False, (
            f"{name} silently enabled conviction sizing (the off-by-default contract)"
        )


def test_swing_keeps_conviction_sizing_off_after_i10_reject() -> None:
    # i10 tested conviction_sizing_enabled=True and REJECTED it (TRAIN degrades, the
    # by-conviction-quartile diagnosis shows sl-rate is not monotone-down with conviction
    # on either coin and the top-conviction Q4 bucket is the worst on SOL — a coin-split
    # signature). The mechanism stays OFF in the swing profile (default-OFF contract).
    target = Settings(_env_file=None)
    apply_profile("swing", target)
    assert target.risk.conviction_sizing_enabled is False
    # The knobs stay in the profile as the documented (rejected) test values, so a future
    # revisit can flip the flag without re-deriving the bounds.
    assert target.risk.conviction_size_min == pytest.approx(0.7)
    assert target.risk.conviction_size_max == pytest.approx(1.4)
    assert target.risk.conviction_size_conf_floor == 0.0


def test_setup_dict_round_trips_persisted_confidence() -> None:
    # Newer setups carry setup_metadata["confidence"]; setup_dict surfaces it for the
    # entry path to read without re-deriving from the size_for buckets.
    class _FakeSetup:
        setup_id = "x"
        plan_id = "p"
        symbol = "ETHUSDT"
        direction = "long"
        status = "active"
        entry_zone_low = Decimal(100.0)
        entry_zone_high = Decimal(110.0)
        stop_loss = Decimal(95.0)
        take_profit = [Decimal(120.0)]
        size_pct = Decimal(0.05)
        hard_rules = []
        soft_rules = []
        invalidation_rules = []
        expires_at = None
        setup_metadata = {"confidence": 0.83}

    d = setup_dict(_FakeSetup())
    assert d["confidence"] == pytest.approx(0.83)


def test_setup_dict_confidence_none_when_missing() -> None:
    # Legacy setups (no setup_metadata key at all) and vetoed-signal setups both yield
    # confidence=None → conviction_size_multiplier returns 1.0 (no-op). Never gate-kill.
    class _LegacySetup:
        setup_id = "x"
        plan_id = "p"
        symbol = "ETHUSDT"
        direction = "long"
        status = "active"
        entry_zone_low = Decimal(100.0)
        entry_zone_high = Decimal(110.0)
        stop_loss = Decimal(95.0)
        take_profit = [Decimal(120.0)]
        size_pct = Decimal(0.05)
        hard_rules = []
        soft_rules = []
        invalidation_rules = []
        expires_at = None
        setup_metadata = {}

    d = setup_dict(_LegacySetup())
    assert d["confidence"] is None

    class _NoMetadataSetup:
        setup_id = "x"
        plan_id = "p"
        symbol = "ETHUSDT"
        direction = "long"
        status = "active"
        entry_zone_low = Decimal(100.0)
        entry_zone_high = Decimal(110.0)
        stop_loss = Decimal(95.0)
        take_profit = [Decimal(120.0)]
        size_pct = Decimal(0.05)
        hard_rules = []
        soft_rules = []
        invalidation_rules = []
        expires_at = None
        setup_metadata = None

    d2 = setup_dict(_NoMetadataSetup())
    assert d2["confidence"] is None