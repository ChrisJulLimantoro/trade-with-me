"""Human-readable engine trace — a flat, chronological plan/confirm/trade narrative.

Separate from the structured stdout logger: this writes an easy-to-read account of
what the engine actually did (which plan, which setup, the confirm decision, and the
resulting trade) to a file you can ``tail -f`` or open afterwards. All functions are
no-ops until ``init`` is called, so non-replay code paths pay nothing.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

_logger: logging.Logger | None = None


def init(path: str) -> None:
    """Point the trace at ``path`` (truncated on open). Idempotent per process."""
    global _logger
    logger = logging.getLogger("ats.trace")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # file only — don't echo into the stdout handler
    for h in list(logger.handlers):
        logger.removeHandler(h)
    handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    _logger = logger


def is_active() -> bool:
    return _logger is not None


def _w(msg: str) -> None:
    if _logger is not None:
        _logger.info(msg)


def _ts(dt: datetime | None) -> str:
    return str(dt)[:19] if dt is not None else "?"


def _sid(value: Any) -> str:
    return str(value)[:8] if value is not None else "????????"


def _num(x: Any) -> str:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return str(x)
    return f"{f:g}"


def _fmt_rule(r: dict[str, Any]) -> str:
    base = f"{r.get('left')} {r.get('operator')} {_num(r.get('right'))}"
    if "weight" in r and r["weight"] is not None:
        base += f" (w{_num(r['weight'])})"
    if "severity" in r:
        when = "on_close" if r.get("on_close") else "intrabar"
        base = f"[{r['severity']}] {base} ({when})"
    return base


def header(title: str) -> None:
    _w("=" * 88)
    _w(title)
    _w("=" * 88)


def block(text: str) -> None:
    """Write a pre-rendered multi-line block verbatim (e.g. the end-of-replay
    summary and trade table). No-op when the trace is inactive."""
    if _logger is None:
        return
    for line in text.rstrip("\n").splitlines():
        _w(line)


def plan(plan_obj: Any, setups: list[Any]) -> None:
    """Record a freshly created plan and its setups."""
    if _logger is None:
        return
    _w("")
    _w(
        f"PLAN CREATED  {_ts(plan_obj.as_of)}  plan={_sid(plan_obj.plan_id)}  "
        f"bias={plan_obj.market_bias}  regime={plan_obj.regime_cell or '-'}  "
        f"setups={len(setups)}"
    )
    if plan_obj.rationale:
        _w(f"  rationale: {plan_obj.rationale}")
    for i, s in enumerate(setups, 1):
        _w(
            f"  setup[{i}] {_sid(s.setup_id)}  {s.direction.upper()}  "
            f"size={_num(s.size_pct)}"
        )
        _w(f"    entry_zone: {_num(s.entry_zone_low)} -> {_num(s.entry_zone_high)}")
        tps = ", ".join(_num(x) for x in s.take_profit)
        _w(f"    take_profit: [{tps}]   stop_loss: {_num(s.stop_loss)}")
        if s.hard_rules:
            _w("    hard:  " + "; ".join(_fmt_rule(r) for r in s.hard_rules))
        if s.soft_rules:
            _w("    soft:  " + "; ".join(_fmt_rule(r) for r in s.soft_rules))
        if s.invalidation_rules:
            _w("    invalidation: " + "; ".join(_fmt_rule(r) for r in s.invalidation_rules))


def confirm(
    *,
    now: datetime | None,
    plan_id: Any,
    setup_id: Any,
    ev: Any,
    action: str,
    reason: str | None,
    size_multiplier: float,
) -> None:
    """Record the confirm decision for a detected setup."""
    if _logger is None:
        return
    _w("-" * 88)
    _w(
        f"CONFIRM  {_ts(now)}  plan={_sid(plan_id)} setup={_sid(setup_id)}  "
        f"action={action}  size_x={_num(size_multiplier)}"
    )
    _w(
        f"  price={_num(ev.price)}  hard_ok={ev.hard_ok}  "
        f"soft_score={_num(ev.soft_score)}"
    )
    if reason:
        _w(f"  reason: {reason}")


def outcome(text: str) -> None:
    """Record what happened after the confirm (executed / skipped + why)."""
    _w(f"  -> {text}")


def trade_opened(
    *,
    now: datetime | None,
    trade_id: Any,
    plan_id: Any,
    setup_id: Any,
    direction: str,
    entry: float,
    size_pct: float,
    margin_usd: float | None = None,
    notional_usd: float | None = None,
    leverage: float | None = None,
) -> None:
    if _logger is None:
        return
    line = (
        f"TRADE OPENED  {_ts(now)}  trade={_sid(trade_id)}  "
        f"plan={_sid(plan_id)} setup={_sid(setup_id)}  "
        f"{direction.upper()} @ {_num(entry)}  size={_num(size_pct)}"
    )
    if margin_usd is not None:
        line += f"  margin=${float(margin_usd):.2f}"
    if notional_usd is not None:
        line += f"  notional=${float(notional_usd):.2f}"
    if leverage is not None:
        line += f"  lev={float(leverage):.2f}x"
    _w(line)


def observe(
    *,
    now: datetime | None,
    trade_id: Any,
    plan_id: Any,
    action: str,
    parse_ok: bool,
    price: float,
    unrealized_pct: float,
    working_stop: float,
    remaining_frac: float,
    confidence: float | None = None,
    reason: str | None = None,
    new_stop: float | None = None,
    new_tp: list[float] | None = None,
    scale_frac: float | None = None,
) -> None:
    """Record every observe_trade decision, including HOLD and parse failures."""
    if _logger is None:
        return
    _w("-" * 88)
    conf = "" if confidence is None else f"  conf={_num(confidence)}"
    _w(
        f"OBSERVE  {_ts(now)}  trade={_sid(trade_id)}  plan={_sid(plan_id)}  "
        f"action={action}  parse_ok={parse_ok}{conf}"
    )
    _w(
        f"  price={_num(price)}  unrealized_margin={unrealized_pct * 100:+.2f}%  "
        f"stop={_num(working_stop)}  remaining={_num(remaining_frac)}"
    )
    details: list[str] = []
    if new_stop is not None:
        details.append(f"new_stop={_num(new_stop)}")
    if new_tp:
        details.append("new_tp=[" + ", ".join(_num(x) for x in new_tp) + "]")
    if scale_frac is not None:
        details.append(f"scale_frac={_num(scale_frac)}")
    if details:
        _w("  proposal: " + "  ".join(details))
    if reason:
        _w(f"  reason: {reason}")


def trade_closed(
    *,
    now: datetime | None,
    trade_id: Any,
    plan_id: Any,
    reason: str,
    pnl_pct: float,
) -> None:
    if _logger is None:
        return
    _w(
        f"TRADE CLOSED  {_ts(now)}  trade={_sid(trade_id)}  plan={_sid(plan_id)}  "
        f"reason={reason}  margin_pnl={pnl_pct * 100:+.2f}%"
    )
