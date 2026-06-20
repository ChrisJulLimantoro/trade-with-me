"""CrossVenueFlow — Binance funding vs peers (cross-exchange divergence).

Persistent same-symbol funding divergence across venues flags participant-mix
asymmetry. A high positive ``funding_divergence_z_30d`` means Binance funding is hot
relative to peers → Binance longs crowded → fade with a short (and vice versa). The
system never executes the cross-venue arb; the divergence is only a directional bias.
Abstains when fewer than 2 peers are available (a single peer can't anchor a median).
"""

from __future__ import annotations

from typing import ClassVar

from ats.agents.base import AgentInput, AgentScore, abstain, clamp01, f

_Z_LEAN = 1.5
_MIN_PEERS = 2


class CrossVenueAgent:
    name: ClassVar[str] = "cross_venue"
    uses_llm: ClassVar[bool] = False

    def run(self, ai: AgentInput) -> AgentScore:
        feats = ai.features or {}
        peers = f(feats.get("funding_peer_count"))
        if peers is None or peers < _MIN_PEERS:
            return abstain(self.name, "insufficient_peers", peer_count=peers)

        z = f(feats.get("funding_divergence_z_30d"))
        if z is None:
            return abstain(self.name, "no_divergence_z", peer_count=peers)

        direction = "short" if z > _Z_LEAN else "long" if z < -_Z_LEAN else "neutral"
        score = clamp01(abs(z) / 3.0) if direction != "neutral" else 0.0
        return AgentScore(
            agent=self.name,
            score=round(score, 6),
            direction=direction,
            deterministic_score=round(score, 6),
            metadata={
                "divergence": f(feats.get("funding_divergence")),
                "divergence_z": z,
                "peer_count": int(peers),
                **({} if direction != "neutral" else {"reason": "divergence_not_extreme"}),
            },
        )
