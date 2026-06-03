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

from pydantic import BaseModel, Field, field_validator, model_validator

Operator = Literal[">", "<", ">=", "<=", "==", "!=", "crosses_above", "crosses_below"]
Severity = Literal["warning", "soft", "hard"]
Direction = Literal["long", "short"]
MarketBias = Literal["bullish", "bearish", "neutral"]
ConfirmAction = Literal["CONFIRM", "REJECT", "WAIT", "REDUCE_SIZE"]
ObserveAction = Literal["HOLD", "TIGHTEN_STOP", "RAISE_TP", "SCALE_OUT", "EXIT_NOW"]
LearningCategory = Literal[
    "false_breakout",
    "stop_too_tight",   # stop inside normal bar noise — price resumed thesis after stop-out
    "funding_misread",
    "regime_shift",
    "liquidity_sweep",
    "alignment_too_low",
    "clean_win",
    "clean_loss",
    "other",
]
DecisionQuality = Literal["excellent", "good", "neutral", "poor"]


# --------------------------------------------------------------------------------------
# Lenient coercion helpers (used by ``mode="before"`` validators).
#
# LLMs routinely emit boundary or null values that are semantically "unset" but violate a
# strict scalar constraint (e.g. ``"scale_frac": 0`` for a non-scale action, ``"confidence":
# null``, ``"size_pct": 1.5``). A bare constraint rejects these and fails the WHOLE parse,
# discarding an otherwise-valid plan/observation. These helpers normalise such values to the
# nearest safe one so the parse survives; structurally unrecoverable problems (bad enums,
# empty take_profit, inverted entry_zone, wrong types) are deliberately left to fail.
# --------------------------------------------------------------------------------------


def _coerce_unit(v: object, default: float) -> object:
    """``ge=0, le=1`` fields: null → ``default``; out-of-range → clamped into [0, 1]."""
    if v is None:
        return default
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return v  # let pydantic raise the clearer type error
    return min(max(f, 0.0), 1.0)


def _coerce_positive_fraction(v: object, default: float) -> object:
    """``gt=0, le=1`` required fields: null/non-positive → ``default``; overshoot → 1.0."""
    if v is None:
        return default
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return v
    if f <= 0:
        return default
    return min(f, 1.0)


def _coerce_optional_fraction(v: object) -> object:
    """``gt=0, le=1`` optional fields: null/non-positive → None ("unset"); overshoot → 1.0."""
    if v is None:
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return v
    if f <= 0:
        return None
    return min(f, 1.0)


def _coerce_str(v: object, default: str = "") -> object:
    """Defaulted string fields: explicit null → ``default`` (left bare otherwise)."""
    return default if v is None else v


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

    @field_validator("size_pct", mode="before")
    @classmethod
    def _normalize_size_pct(cls, v: object) -> object:
        """``size_pct`` is advisory — risk-based sizing overrides it (see prompts).

        So a stray ``0`` / ``1.5`` from the model should never sink the whole plan: collapse
        non-positive/null to a nominal 0.05 and clamp an overshoot to 1.0.
        """
        return _coerce_positive_fraction(v, 0.05)

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

    @field_validator("rationale", mode="before")
    @classmethod
    def _null_rationale(cls, v: object) -> object:
        return _coerce_str(v)


class ConfirmOutput(BaseModel):
    """The confirm_setup result. ``size_multiplier`` only applies to REDUCE_SIZE."""

    action: ConfirmAction
    reason: str = ""
    size_multiplier: float = Field(default=1.0, gt=0, le=1)

    @field_validator("reason", mode="before")
    @classmethod
    def _null_reason(cls, v: object) -> object:
        return _coerce_str(v)

    @field_validator("size_multiplier", mode="before")
    @classmethod
    def _normalize_size_multiplier(cls, v: object) -> object:
        """Null/non-positive → full size (1.0); overshoot → clamped to 1.0.

        A bad multiplier should fall back to "trade full size" rather than fail the confirm.
        """
        return _coerce_positive_fraction(v, 1.0)


class ObservationOutput(BaseModel):
    """The observe_trade result — a tactical adjustment for an open trade.

    All fields beyond ``action`` are advisory; the engine clamps them so the agent can only
    protect or extend a position, never widen its risk. ``new_stop``/``new_tp`` are used by
    TIGHTEN_STOP/RAISE_TP; ``scale_frac`` by SCALE_OUT; ``confidence`` gates EXIT_NOW.
    """

    action: ObserveAction
    reason: str = ""
    new_stop: float | None = None
    new_tp: list[float] | None = None
    scale_frac: float | None = Field(default=None, gt=0, le=1)
    confidence: float = Field(default=0.0, ge=0, le=1)

    @field_validator("reason", mode="before")
    @classmethod
    def _null_reason(cls, v: object) -> object:
        return _coerce_str(v)

    @field_validator("scale_frac", mode="before")
    @classmethod
    def _coerce_scale_frac(cls, v: object) -> object:
        """Normalise ``scale_frac`` to ``None`` when it means "no scale-out".

        Models routinely emit ``"scale_frac": 0`` / ``0.0`` / ``null`` for non-SCALE_OUT
        actions (HOLD, TIGHTEN_STOP, ...). A bare ``gt=0`` field rejects ``0`` outright —
        failing the *entire* observation parse — and the ``None`` default only applies when
        the key is absent, not when it's explicitly present. Treat any non-positive value as
        "unset", and clamp an overshoot to the ceiling. The engine clamps again before acting.
        """
        return _coerce_optional_fraction(v)

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_null_confidence(cls, v: object) -> object:
        """Null → 0.0 (the safe default keeps EXIT_NOW gated); out-of-range → clamped to [0, 1].

        Some models return ``"confidence": null`` for non-EXIT_NOW actions; an explicit null is
        rejected by a bare float field even with a default set (the default only applies when
        the key is absent), and an over-confident ``1.3`` would also fail the constraint.
        """
        return _coerce_unit(v, 0.0)


class ReflectionOutput(BaseModel):
    """The reflect_trade result — one structured lesson from a closed trade."""

    category: LearningCategory
    hypothesis: str
    evidence: str = ""
    proposed_adjustment: str = Field(default="", max_length=200)
    confidence_in_lesson: float = Field(default=0.5, ge=0, le=1)
    decision_quality: DecisionQuality = "neutral"

    @field_validator("hypothesis", "evidence", mode="before")
    @classmethod
    def _null_text(cls, v: object) -> object:
        return _coerce_str(v)

    @field_validator("proposed_adjustment", mode="before")
    @classmethod
    def _normalize_adjustment(cls, v: object) -> object:
        """Null → ""; truncate to the 200-char ceiling instead of rejecting an over-long tip."""
        if v is None:
            return ""
        return v[:200] if isinstance(v, str) else v

    @field_validator("confidence_in_lesson", mode="before")
    @classmethod
    def _normalize_confidence(cls, v: object) -> object:
        return _coerce_unit(v, 0.5)


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
