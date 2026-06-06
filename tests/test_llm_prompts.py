"""Guards for the strategist system-prompt assembly (strategy-catalog injection)."""

from __future__ import annotations

from ats.llm.prompts import _strategy_catalog, plan_system_prompt


def test_strategy_catalog_loads_non_empty():
    assert _strategy_catalog().strip()


def test_plan_prompt_injects_catalog_before_schema():
    p = plan_system_prompt()
    assert "SETUP REFERENCE" in p
    assert "FVG" in p  # a known catalog token
    # The JSON schema instruction must remain the final section of the prompt.
    assert p.rstrip().endswith("}")
    assert p.index("SETUP REFERENCE") < p.index("must exactly match this schema")
