"""Engine DTOs — per-tick and per-replay result records.

Plain dataclasses with no engine dependencies, so every collaborator (orchestrator,
exit manager, observer, closing path, replay loop) can depend on the report shape
without pulling in the runtime.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ClosedTradeInfo:
    """Minimal closed-trade data emitted by close_trade for metrics collection."""

    direction: str
    entry_price: float
    stop_loss: float
    exit_price: float
    exit_reason: str
    margin_usd: float
    notional_usd: float
    leverage: float | None
    pnl_pct: float
    pnl_usd: float
    atr_at_close: float | None  # atr_14 from the bar that closed the trade


@dataclass
class TickReport:
    now: datetime | None = None
    plan_id: uuid.UUID | None = None
    detections: int = 0
    opened: int = 0
    closed: int = 0
    confirm_calls: int = 0
    observe_calls: int = 0
    risk_rejected: int = 0
    plan_invalidated: bool = False
    paused: bool = False
    closed_trades: list[ClosedTradeInfo] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class TradeOutcome:
    """Lightweight summary captured for each closed trade during replay."""

    direction: str
    entry_price: float
    stop_loss: float
    exit_price: float
    exit_reason: str
    margin_usd: float
    notional_usd: float
    leverage: float | None
    pnl_pct: float  # return on committed margin, signed
    pnl_usd: float
    atr_at_entry: float | None  # atr_14 at the bar we entered
    stop_dist_pts: float  # abs(entry - stop_loss) in price points


@dataclass
class ReplayReport:
    symbol: str
    timeframe: str
    bars: int = 0
    plans_created: int = 0
    plans_with_setups: int = 0
    detections: int = 0
    opened: int = 0
    closed: int = 0
    invalidations: int = 0
    confirm_calls: int = 0
    observe_calls: int = 0
    risk_rejected: int = 0
    trade_outcomes: list[TradeOutcome] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # --- derived metrics (populated by finalise()) ---
    win_rate: float = 0.0
    avg_pnl_pct: float = 0.0  # average return on committed margin
    expectancy_pct: float = 0.0  # margin avg_win * win_rate + avg_loss * loss_rate
    avg_stop_dist_atr: float | None = None  # avg stop distance in ATR multiples
    volatility_pct: float | None = None  # std-dev of per-trade margin returns (%)
    sharpe_ratio: float | None = None  # avg_pnl / std(pnl), risk-free = 0, trade-level
    sortino_ratio: float | None = None  # avg_pnl / downside-std, risk-free = 0, trade-level

    def finalise(self) -> None:
        """Compute summary metrics from trade_outcomes. Call once after the loop."""
        if not self.trade_outcomes:
            return
        wins = [t for t in self.trade_outcomes if t.pnl_pct > 0]
        losses = [t for t in self.trade_outcomes if t.pnl_pct <= 0]
        n = len(self.trade_outcomes)
        returns = [t.pnl_pct for t in self.trade_outcomes]
        self.win_rate = len(wins) / n
        self.avg_pnl_pct = sum(returns) / n
        avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0.0
        self.expectancy_pct = avg_win * self.win_rate + avg_loss * (1 - self.win_rate)
        atr_multiples = [
            t.stop_dist_pts / t.atr_at_entry
            for t in self.trade_outcomes
            if t.atr_at_entry and t.atr_at_entry > 0
        ]
        if atr_multiples:
            self.avg_stop_dist_atr = sum(atr_multiples) / len(atr_multiples)

        if n >= 2:
            variance = sum((r - self.avg_pnl_pct) ** 2 for r in returns) / (n - 1)
            std = math.sqrt(variance)
            self.volatility_pct = std * 100
            if std > 0:
                self.sharpe_ratio = self.avg_pnl_pct / std
            downside = [r for r in returns if r < 0]
            if len(downside) >= 2:
                d_var = sum(r ** 2 for r in downside) / (len(downside) - 1)
                d_std = math.sqrt(d_var)
                if d_std > 0:
                    self.sortino_ratio = self.avg_pnl_pct / d_std
