"""Hand-set agent weights — version-controlled, anti-tuning.

The eight weights sum to 1.0 and are NOT search targets: the replay harness *measures*
contribution, it does not authorize re-tuning. The only sanctioned change is the
decision gate **dropping** an agent whose ablation contribution is ≤ 0, after which the
survivors are renormalized arithmetically (each ÷ sum of survivors), never re-discovered.
"""

from __future__ import annotations

# M1 weights — nine agents (spec 04 §Synthesizer + the HTF-trend voter). Sum must be 1.0.
# The original eight are diluted uniformly to 0.75 of their mass so their *relative* shape
# is untouched (no per-agent re-tuning); the freed 0.25 goes to ``htf_trend``, making the
# dominant higher-timeframe trend a quarter of the direction vote. This biases the
# 15m-myopic synthesizer with the macro trend without re-discovering any single weight.
WEIGHTS: dict[str, float] = {
    "structure": 0.1875,
    "momentum": 0.1125,
    "funding": 0.075,
    "liquidity": 0.0375,
    "price_action": 0.1125,
    "cross_venue": 0.1125,
    "basis": 0.075,
    "cvd": 0.0375,
    "htf_trend": 0.25,
}

# Fail loudly at import if the weights drift off the simplex.
_total = round(sum(WEIGHTS.values()), 9)
if _total != 1.0:
    raise ValueError(f"agent weights must sum to 1.0, got {_total}")


def renormalize(active: set[str]) -> dict[str, float]:
    """Renormalize the weights of an active subset to sum to 1.0.

    Used only after the decision gate drops an ablation-negative agent. Pure arithmetic:
    each surviving weight ÷ the sum of surviving weights.
    """
    kept = {k: v for k, v in WEIGHTS.items() if k in active}
    total = sum(kept.values())
    if total <= 0:
        raise ValueError(f"no positive weight in active set {sorted(active)}")
    return {k: v / total for k, v in kept.items()}


def weights_with_htf(w: float) -> dict[str, float]:
    """Return WEIGHTS with ``htf_trend`` overridden to ``w``.

    Mirrors how the current 0.25 was introduced: the eight base weights are scaled by
    ``(1 - w) / (their current sum)`` so their *relative* shape is untouched, and the
    freed/reclaimed mass goes to ``htf_trend``. Replay-only override (plan.htf_trend_weight).
    """
    if not 0 < w < 1:
        raise ValueError(f"htf_trend weight must be in (0, 1), got {w}")
    base = {k: v for k, v in WEIGHTS.items() if k != "htf_trend"}
    base_total = sum(base.values())
    scale = (1 - w) / base_total
    out = {k: v * scale for k, v in base.items()}
    out["htf_trend"] = w
    return out
