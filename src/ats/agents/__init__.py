"""Deterministic signal agents (spec 04, 15m-bridged).

Eight narrow scorers run on the same :class:`AgentInput` and each emit an
:class:`AgentScore`. No LLM, no network — every field reduces to spec-02 features
and spec-01 candles, so a replay is byte-reproducible. The synthesizer
(``ats.synthesis``) combines the eight scores into one signal.
"""

from __future__ import annotations

from ats.agents.base import AgentInput, AgentScore
from ats.agents.basis import BasisAgent
from ats.agents.cross_venue import CrossVenueAgent
from ats.agents.cvd import CvdAgent
from ats.agents.funding import FundingAgent
from ats.agents.htf_trend import HtfTrendAgent
from ats.agents.liquidity import LiquidityAgent
from ats.agents.momentum import MomentumAgent
from ats.agents.price_action import PriceActionAgent
from ats.agents.structure import StructureAgent

# Registry keyed by the agent name used in weights.py / the synthesizer. Order is the
# scoring order (stable for reproducible logs).
AGENTS = [
    StructureAgent(),
    MomentumAgent(),
    FundingAgent(),
    LiquidityAgent(),
    PriceActionAgent(),
    CrossVenueAgent(),
    BasisAgent(),
    CvdAgent(),
    HtfTrendAgent(),
]

__all__ = [
    "AGENTS",
    "AgentInput",
    "AgentScore",
    "BasisAgent",
    "CrossVenueAgent",
    "CvdAgent",
    "FundingAgent",
    "HtfTrendAgent",
    "LiquidityAgent",
    "MomentumAgent",
    "PriceActionAgent",
    "StructureAgent",
]
