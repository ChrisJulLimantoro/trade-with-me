"""Tests for the episodic-memory building blocks: fingerprint + post-mortem mock."""

from __future__ import annotations

from ats.learning.fingerprint import FINGERPRINT_DIM, build_fingerprint, to_vector_literal
from ats.llm.mock_data import canned_reflection
from ats.llm.schemas import ReflectionOutput

# --- fingerprint ------------------------------------------------------------------------

def _feature_row(**overrides: float) -> dict:
    base = {
        "pr_atr": 0.3, "pr_rsi": 0.6, "pr_vol_zscore": 0.5, "pr_oi_delta": 0.4,
        "pr_funding_imbalance": 0.7, "pr_momentum": 0.8, "pr_cvd_divergence": 0.2,
        "rsi_14": 58.0, "macd_hist": 0.4, "momentum_composite": 0.65,
        "funding_z_30d": 1.2, "basis_z_30d": -0.5,
    }
    base.update(overrides)
    return base


def test_fingerprint_has_fixed_length() -> None:
    assert len(build_fingerprint(_feature_row())) == FINGERPRINT_DIM


def test_fingerprint_is_deterministic() -> None:
    row = _feature_row()
    assert build_fingerprint(row) == build_fingerprint(row)


def test_fingerprint_components_in_unit_range() -> None:
    for x in build_fingerprint(_feature_row()):
        assert 0.0 <= x <= 1.0


def test_fingerprint_handles_missing_features() -> None:
    fp = build_fingerprint({})
    assert len(fp) == FINGERPRINT_DIM
    # Missing → neutral 0.5 everywhere except the macd-direction flag (0.0 when not > 0).
    assert all(0.0 <= x <= 1.0 for x in fp)


def test_fingerprint_macd_direction_flag() -> None:
    up = build_fingerprint(_feature_row(macd_hist=1.0))
    down = build_fingerprint(_feature_row(macd_hist=-1.0))
    assert up != down  # the macd-direction component differs


def test_vector_literal_format() -> None:
    lit = to_vector_literal([0.1, 0.2, 0.3])
    assert lit.startswith("[") and lit.endswith("]")
    assert lit == "[0.100000,0.200000,0.300000]"


# --- post-mortem mock -------------------------------------------------------------------

def test_reflection_clean_win() -> None:
    out = canned_reflection({"pnl_pct": 0.03, "exit_reason": "tp", "direction": "long"})
    assert isinstance(out, ReflectionOutput)
    assert out.category == "clean_win"
    assert out.decision_quality == "good"


def test_reflection_clean_loss() -> None:
    out = canned_reflection({"pnl_pct": -0.02, "exit_reason": "sl", "direction": "long"})
    assert out.category == "clean_loss"
    assert out.decision_quality == "poor"


def test_reflection_regime_shift_on_invalidation() -> None:
    out = canned_reflection({"pnl_pct": -0.01, "exit_reason": "invalidation"})
    assert out.category == "regime_shift"


def test_reflection_proposed_adjustment_capped() -> None:
    # The schema caps proposed_adjustment at 200 chars by truncating (not rejecting), so an
    # over-long tip never sinks the whole reflection.
    r = ReflectionOutput(category="other", hypothesis="x", proposed_adjustment="z" * 201)
    assert len(r.proposed_adjustment) == 200
