"""Tests for profile-specific strategy prompt guidance."""

from __future__ import annotations

from ats.config import Settings, settings
from ats.llm import prompts
from ats.strategy_profiles import apply_profile


def test_scalper_profile_sets_strategy_metadata() -> None:
    target = Settings(_env_file=None)

    applied = apply_profile("scalper", target)

    assert applied["strategy_profile"] == "scalper"
    assert target.strategy_profile == "scalper"
    assert target.max_setups_per_plan == 3


def test_plan_prompt_includes_profile_addendum(monkeypatch) -> None:
    monkeypatch.setattr(settings, "strategy_profile", "scalper")
    monkeypatch.setattr(settings, "max_setups_per_plan", 3)

    prompt = prompts.plan_system_prompt()

    assert "PROFILE GUIDANCE (scalper)" in prompt
    assert "up to 3 distinct allowed_setups" in prompt
    assert "sideways regimes" in prompt
    assert "range mean-reversion" in prompt
    assert "support/range lows" in prompt
    assert "range midpoint" in prompt
    assert "VWAP" in prompt
    assert "avoid breakout-continuation" in prompt
