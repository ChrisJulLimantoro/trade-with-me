"""Guards for the LLM system-prompt assembly.

Since Part 2 the only plan-time prompt is the bounded adjudication judge — it must frame the
LLM as a veto/bias over the deterministic signal, never an author.
"""

from __future__ import annotations

from ats.llm.prompts import ADJUDICATE_SYSTEM_PROMPT, user_message


def test_adjudicate_prompt_bounds_the_judge():
    p = ADJUDICATE_SYSTEM_PROMPT
    # The hard ±0.20 budget and the no-authoring contract must be stated in the prompt.
    assert "[-0.20, 0.20]" in p
    assert "cannot change the direction or move any level" in p
    assert "DO NOT author trades" in p
    # The JSON schema instruction must remain the final section of the prompt.
    assert p.rstrip().endswith("}")
    assert p.index("confidence_delta") < p.index("must exactly match this schema")


def test_user_message_is_compact_json():
    msg = user_message({"a": 1, "b": [2, 3]})
    assert msg == '{"a":1,"b":[2,3]}'
