"""Agent contract — the shared shape every deterministic scorer implements.

Spec 04 defines ``AgentInput → AgentScore``. The 15m bridge keeps the contract but
feeds the agents the *envelope's native types* (the list-of-dict candles and the
pruned feature dict that ``build_envelope`` already produces) rather than pandas
frames, so no conversion layer is needed and determinism is trivial.

``run`` is synchronous: every agent is pure compute over the input with no I/O, so
there is nothing to await. (Spec 04 wrote it ``async``; the bridge drops that — see
``docs/redesign/01-deterministic-strategist.md``.)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Literal, Protocol

Direction = Literal["long", "short", "neutral"]


@dataclass
class AgentInput:
    """Shared input for all eight agents.

    ``features`` is the pruned 15m feature row, ``recent_ohlcv`` the 15m candles
    oldest→newest, ``higher_timeframes`` the ``{"1h": {...}, "4h": {...}}`` snapshots
    (each with ``features`` + ``recent_ohlcv``). Only the candle agents read the HTF
    block; the flow agents (Funding/CrossVenue/Basis) are timeframe-agnostic.
    """

    symbol: str
    cycle_ts: datetime
    timeframe_primary: str
    features: dict[str, Any]
    recent_ohlcv: list[dict[str, Any]]
    regime: dict[str, Any] = field(default_factory=dict)
    higher_timeframes: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentScore:
    """One agent's verdict. ``score`` is 0..1; ``direction`` may be neutral (abstain)."""

    agent: str
    score: float
    direction: Direction
    deterministic_score: float
    llm_delta: float | None = None  # always None in M1 (spec 07 populates it)
    metadata: dict[str, Any] = field(default_factory=dict)


class Agent(Protocol):
    name: ClassVar[str]
    uses_llm: ClassVar[bool]

    def run(self, ai: AgentInput) -> AgentScore: ...


# --- shared numeric helpers ----------------------------------------------------------


def f(value: Any) -> float | None:
    """Coerce to a finite float, or None for missing/NaN/non-numeric."""
    try:
        x = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return None if x is None or math.isnan(x) else x


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def abstain(name: str, reason: str, **metadata: Any) -> AgentScore:
    """A zero-score neutral verdict (insufficient/missing input)."""
    return AgentScore(
        agent=name,
        score=0.0,
        direction="neutral",
        deterministic_score=0.0,
        metadata={"reason": reason, **metadata},
    )


def primary_close(ai: AgentInput) -> float | None:
    """Most recent 15m close — features ``close``/``price`` then the last candle."""
    feats = ai.features or {}
    for key in ("close", "price"):
        v = f(feats.get(key))
        if v is not None:
            return v
    if ai.recent_ohlcv:
        return f(ai.recent_ohlcv[-1].get("close"))
    return None
