"""Pydantic I/O schemas for the LLM-plan trading layer.

These are the *contract* with the LLM: create_plan returns a ``PlanOutput``,
confirm_setup returns a ``ConfirmOutput``. The OpenAI client validates raw model
output against these; a validation failure means we fall back to deterministic
behaviour rather than trusting unstructured text.

The same rule shapes (``Rule`` / ``InvalidationRule``) are what the deterministic
rule engine evaluates at run time — so a plan the LLM emits is directly executable.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Operator = Literal[">", "<", ">=", "<=", "==", "!=", "crosses_above", "crosses_below"]
Severity = Literal["warning", "soft", "hard"]
Direction = Literal["long", "short"]
MarketBias = Literal["bullish", "bearish", "neutral"]
ConfirmAction = Literal["CONFIRM", "REJECT", "WAIT", "REDUCE_SIZE"]


class Rule(BaseModel):
    """A single comparison the rule engine can evaluate.

    ``left``/``right`` resolve to a feature column name, the literal token
    ``"price"``, or a numeric literal. ``weight`` is used only for soft rules.
    """

    left: str
    operator: Operator
    right: float | str
    weight: float | None = None


class InvalidationRule(BaseModel):
    """A condition that, when true, degrades or kills a plan/setup.

    ``on_close`` rules only fire on a closed candle (no single-tick whipsaws).
    """

    severity: Severity
    left: str
    operator: Operator
    right: float | str
    on_close: bool = True


class SetupOutput(BaseModel):
    """One executable trade setup within a plan."""

    direction: Direction
    entry_zone: list[float] = Field(min_length=2, max_length=2)  # [low, high]
    take_profit: list[float] = Field(min_length=1)
    stop_loss: float
    size_pct: float = Field(gt=0, le=1)
    hard_rules: list[Rule] = Field(default_factory=list)
    soft_rules: list[Rule] = Field(default_factory=list)
    invalidation_rules: list[InvalidationRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_zone(self) -> SetupOutput:
        low, high = self.entry_zone
        if low > high:
            raise ValueError("entry_zone must be [low, high] with low <= high")
        return self


class PlanOutput(BaseModel):
    """The full create_plan result."""

    market_bias: MarketBias
    rationale: str = ""
    allowed_setups: list[SetupOutput] = Field(default_factory=list)


class ConfirmOutput(BaseModel):
    """The confirm_setup result. ``size_multiplier`` only applies to REDUCE_SIZE."""

    action: ConfirmAction
    reason: str = ""
    size_multiplier: float = Field(default=1.0, gt=0, le=1)


class LlmResult(BaseModel):
    """Audit envelope returned alongside every parsed LLM output (real or mock)."""

    parse_ok: bool
    model: str
    mock: bool
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int | None = None
    raw: dict[str, object] | None = None  # raw parsed response for the audit row
