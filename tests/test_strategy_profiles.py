"""Tests for named strategy profiles."""

from __future__ import annotations

from ats.config import Settings
from ats.strategy_profiles import apply_profile


def test_scalper_profile_sets_strategy_metadata() -> None:
    target = Settings(_env_file=None)

    applied = apply_profile("scalper", target)

    assert applied["strategy_profile"] == "scalper"
    assert target.strategy_profile == "scalper"
    assert target.plan.max_setups_per_plan == 3


def test_scalper_profiles_enable_time_stop() -> None:
    # Defect 0.1: max_hold_bars must be a positive bound, not 0 (which disables the time-stop
    # in _advance_trade). Both scalper variants set the intended ~2h bound on 15m.
    for name in ("scalper", "scalper_v2"):
        target = Settings(_env_file=None)
        apply_profile(name, target)
        assert target.exits.max_hold_bars > 0, f"{name} silently disables the time-stop"
        assert target.exits.max_hold_bars == 8


def test_scalper_promotes_event_gated_observer() -> None:
    # Part 2 Role 2: the exit-strategy observer is event-gated in the shipped scalper, not
    # only the scalper_v2 A/B variant (it was 99.5% no-op at per-bar cadence).
    for name in ("scalper", "scalper_v2"):
        target = Settings(_env_file=None)
        apply_profile(name, target)
        assert target.observer.observe_only_on_health is True, (
            f"{name} runs the observer every bar"
        )
