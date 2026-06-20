"""Hand-set agent weights — version-controlled, anti-tuning.

The eight weights sum to 1.0 and are NOT search targets: the replay harness *measures*
contribution, it does not authorize re-tuning. The only sanctioned change is the
decision gate **dropping** an agent whose ablation contribution is ≤ 0, after which the
survivors are renormalized arithmetically (each ÷ sum of survivors), never re-discovered.
"""

from __future__ import annotations

# M1 weights — eight agents (spec 04 §Synthesizer). Sum must be 1.0.
WEIGHTS: dict[str, float] = {
    "structure": 0.25,
    "momentum": 0.15,
    "funding": 0.10,
    "liquidity": 0.05,
    "price_action": 0.15,
    "cross_venue": 0.15,
    "basis": 0.10,
    "cvd": 0.05,
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
