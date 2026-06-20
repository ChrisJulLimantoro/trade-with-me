"""Small trade-quantity helpers shared across the engine collaborators.

Pure conversions between a :class:`~ats.db.models.PaperTrade`'s stored notional/margin
and the percentage-return bookkeeping used by the exit machine and observer. Kept in one
leaf module so the focused engine modules depend on these without depending on each other.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from ats.config import settings
from ats.db.models import PaperTrade


def _f(v: Any) -> float:
    return float(v) if isinstance(v, Decimal) else float(v)


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _round_or_none(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(value, digits)


def _trade_notional_margin(trade: PaperTrade) -> tuple[float, float]:
    notional_usd = (
        _f(trade.notional_usd)
        if trade.notional_usd is not None
        else settings.risk.paper_equity_usd * _f(trade.size_pct)
    )
    if trade.margin_usd is not None:
        margin_usd = _f(trade.margin_usd)
    elif trade.leverage is not None and _f(trade.leverage) > 0:
        margin_usd = notional_usd / _f(trade.leverage)
    else:
        margin_usd = notional_usd
    return notional_usd, margin_usd


def _margin_pnl_pct(trade: PaperTrade, notional_pnl_pct: float) -> float:
    notional_usd, margin_usd = _trade_notional_margin(trade)
    return (notional_usd * notional_pnl_pct) / margin_usd if margin_usd > 0 else 0.0
