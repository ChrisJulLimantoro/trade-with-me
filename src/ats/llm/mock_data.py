"""Deterministic mock LLM outputs.

The mock turns a structured envelope into a valid response WITHOUT any network call, so
the entire pipeline (deterministic proposer → adjudicate → risk → paper executor →
reconciliation) and all tests run with no API key and zero token spend. Output is a pure
function of the envelope, so replays are reproducible.

Since Part 2 the mock no longer authors plans (the deterministic 8-agent engine does).
``canned_adjudication`` is the plan-time mock: it returns ``confidence_delta = 0`` — the
pure deterministic baseline (the control group) — so under the mock the bounded judge is a
no-op and the run is fully reproducible.
"""

from __future__ import annotations

from typing import Any

from ats.config import settings
from ats.llm.schemas import (
    AdjudicationOutput,
    ObservationOutput,
    ReflectionOutput,
)


def canned_adjudication(envelope: dict[str, Any]) -> AdjudicationOutput:
    """The control-group judgement: no adjustment, no veto (``confidence_delta = 0``).

    A real LLM would weigh the deterministic signal against context; the mock leaves the
    signal untouched so ``llm_mock=True`` is exactly the deterministic baseline. ``bias``
    echoes the signal direction (so it agrees and is never ignored) and ``reasons`` carry the
    synthesizer's own reasons through unchanged.
    """
    sig = envelope.get("signal") or {}
    direction = sig.get("direction")
    bias = {"long": "bullish", "short": "bearish"}.get(str(direction), "neutral")
    return AdjudicationOutput(
        confidence_delta=0.0,
        bias=bias,
        no_trade=False,
        reasons=list(sig.get("reasons") or []),
    )


def canned_observation(envelope: dict[str, Any]) -> ObservationOutput:
    """Deterministic exit-management decision from unrealized P&L + momentum.

    Mirrors a sensible discretionary trader: protect a healthy gain when momentum fades,
    let a strong winner run, and bail when momentum has clearly flipped against the trade.
    Pure function of the envelope, so replays stay reproducible.
    """
    direction = envelope.get("direction", "long")
    unrealized = float(
        envelope.get("unrealized_margin_pct", envelope.get("unrealized_pct", 0.0))
    )
    price = float(envelope.get("current_price", 0.0))
    feats = envelope.get("features_now") or {}
    macd_hist = feats.get("macd_hist")
    mh = float(macd_hist) if macd_hist is not None else 0.0
    # Momentum "with the trade" is positive macd_hist for longs, negative for shorts.
    momentum_with = mh if direction == "long" else -mh
    progress = envelope.get("progress") or {}
    observer_context = envelope.get("observer_context") or {}
    thesis_health = observer_context.get("thesis_health") or {}
    excursion = observer_context.get("excursion") or {}
    pressure = str(observer_context.get("recommended_pressure") or "hold")
    hold = envelope.get("hold") or {}
    hold_progress = float(
        hold.get("current_window_progress")
        or hold.get("hold_progress")
        or 0.0
    )
    max_favorable = float(progress.get("max_favorable_pnl_pct") or 0.0)
    stale_candidate = bool(progress.get("stale_candidate")) or (
        hold_progress >= settings.observer.observe_stale_hold_progress
        and abs(unrealized) <= settings.observer.observe_stale_unrealized_abs_pct
        and max_favorable <= settings.observer.observe_stale_mfe_pct
    )
    current_r = excursion.get("current_r")
    mfe_r = excursion.get("mfe_r")
    failed_to_travel = (
        current_r is not None
        and mfe_r is not None
        and float(current_r) <= -0.5
        and float(mfe_r) < 0.25
    )

    if pressure == "exit" or thesis_health.get("status") == "broken":
        return ObservationOutput(
            action="EXIT_NOW",
            reason="Observer context marks thesis broken or exit pressure.",
            confidence=0.85,
        )
    if pressure == "cut_or_tighten" and (unrealized <= 0.0 or failed_to_travel):
        return ObservationOutput(
            action="EXIT_NOW",
            reason="Trade is red or failed to travel under cut-or-tighten pressure.",
            confidence=0.78,
        )

    # Clear reversal against an at-risk position → exit.
    if unrealized <= 0.0 and momentum_with < 0:
        return ObservationOutput(
            action="EXIT_NOW",
            reason="Momentum flipped against the trade while not in profit.",
            confidence=0.8,
        )
    # Opportunity-cost exit: the trade used most of its hold budget and never moved.
    if stale_candidate and momentum_with <= 0:
        return ObservationOutput(
            action="EXIT_NOW",
            reason="Trade is stale: most of the hold window is spent without favorable progress.",
            confidence=0.75,
        )
    # Healthy gain but momentum fading → lock in by tightening toward price.
    if unrealized >= 0.01 and momentum_with <= 0:
        # Pull the stop to roughly halfway between entry and current price.
        entry = float(envelope.get("entry_price", price))
        new_stop = round(entry + (price - entry) * 0.5, 8)
        return ObservationOutput(
            action="TIGHTEN_STOP",
            reason=f"Banking gains: unrealized {unrealized:+.2%}, momentum fading.",
            new_stop=new_stop,
        )
    # Strong continuation → let it run a bit further.
    if unrealized >= 0.015 and momentum_with > 0:
        tps = envelope.get("take_profit") or []
        if tps:
            final = float(tps[-1])
            extended = round(final * (1.01 if direction == "long" else 0.99), 8)
            return ObservationOutput(
                action="RAISE_TP",
                reason="Strong momentum with open profit; extend the final target.",
                new_tp=[extended],
            )
    return ObservationOutput(action="HOLD", reason="No clear edge to adjust this check.")


def canned_reflection(envelope: dict[str, Any]) -> ReflectionOutput:
    """Deterministic post-mortem from the closed trade's exit_reason + pnl.

    Accepts the trade fields either at the top level or nested under a "trade" key (the
    shape the post-mortem builder emits).
    """
    trade = envelope.get("trade") or envelope
    pnl = float(trade.get("pnl_pct", 0.0))
    reason = str(trade.get("exit_reason", ""))
    win = pnl > 0

    if reason in ("tp", "observe_exit") and win:
        category, quality = "clean_win", "good"
    elif reason == "sl" and not win:
        category, quality = "clean_loss", "poor"
    elif reason == "invalidation":
        category, quality = "regime_shift", "neutral"
    elif reason == "expiry":
        category, quality = "alignment_too_low", "neutral"
    else:
        category, quality = "other", "neutral"

    return ReflectionOutput(
        category=category,
        hypothesis=f"Trade closed via {reason or 'unknown'} at {pnl:+.2%}.",
        evidence=(
            f"direction={trade.get('direction')} "
            f"entry={trade.get('entry_price')} exit={trade.get('exit_price')}"
        ),
        proposed_adjustment="",
        confidence_in_lesson=0.4,
        decision_quality=quality,
    )
