"""Deterministic proposer: envelope → PlanOutput bridge + layer-purity guard."""

from __future__ import annotations

from datetime import datetime

from ats.llm.schemas import PlanOutput
from ats.strategy.deterministic import propose_plan

T0 = datetime(2026, 1, 1, 12, 0)


def _candle(h, lo, c, v=1000.0):
    return {"open": c, "high": h, "low": lo, "close": c, "volume": v}


def _breakout_envelope() -> dict:
    bars = [_candle(100, 98, 99) for _ in range(20)] + [_candle(106, 104, 105, v=2000)]
    return {
        "as_of": T0,
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "features": {
            "close": 105.0,
            "atr_14": 2.0,
            "vol_zscore_20": 2.0,
            "rsi_14": 62.0,
            "macd_hist": 0.6,
            "momentum_composite": 0.8,
        },
        "regime": {"regime_cell": "bull-low"},
        "recent_ohlcv": bars,
        "higher_timeframes": {},
        "risk_limits": {"allowed_directions": ["long", "short"]},
    }


def test_proposer_emits_long_plan_on_breakout() -> None:
    plan, result = propose_plan(_breakout_envelope(), symbol="BTCUSDT")
    assert isinstance(plan, PlanOutput)
    assert result.model == "deterministic" and result.parse_ok
    assert plan.market_bias == "bullish"
    assert len(plan.allowed_setups) == 1
    setup = plan.allowed_setups[0]
    assert setup.direction == "long"
    assert setup.entry_zone[0] < setup.entry_zone[1]


def test_proposer_is_deterministic() -> None:
    a, _ = propose_plan(_breakout_envelope(), symbol="BTCUSDT")
    b, _ = propose_plan(_breakout_envelope(), symbol="BTCUSDT")
    assert a.model_dump() == b.model_dump()


def test_regime_gate_drops_disallowed_direction() -> None:
    env = _breakout_envelope()
    env["risk_limits"]["allowed_directions"] = ["short"]  # long breakout is now disallowed
    plan, _ = propose_plan(env, symbol="BTCUSDT")
    assert plan.allowed_setups == []
    assert plan.market_bias == "neutral"


def test_signal_layer_imports_no_llm_transport() -> None:
    # Spec 04: the deterministic signal layer must not depend on the LLM transport
    # (ats.llm.client / openai / anthropic). schemas.py — the shared contract — is allowed.
    from pathlib import Path

    import ats.agents as agents_pkg
    import ats.orchestration as orch_pkg
    import ats.synthesis as synth_pkg

    forbidden = ("ats.llm.client", "import openai", "import anthropic")
    for pkg in (agents_pkg, orch_pkg, synth_pkg):
        root = Path(pkg.__file__).parent
        for path in root.rglob("*.py"):
            src = path.read_text()
            for token in forbidden:
                assert token not in src, f"{path} imports forbidden transport {token!r}"
