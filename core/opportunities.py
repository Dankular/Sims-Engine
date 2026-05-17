from __future__ import annotations

from dataclasses import dataclass, field
import random


@dataclass
class Opportunity:
    opportunity_id: str
    trigger: str
    requirements: dict
    rewards: dict
    expires_tick: int
    status: str = "active"
    metadata: dict = field(default_factory=dict)


class OpportunityManager:
    def __init__(self) -> None:
        self._active: dict[str, list[Opportunity]] = {}

    def tick(self, engine) -> None:
        now = engine.tick_count
        for sim in engine.sims:
            bucket = self._active.setdefault(sim.sim_id, [])
            for opp in bucket:
                if opp.status == "active" and now >= opp.expires_tick:
                    opp.status = "expired"
            if random.random() < 0.03:
                opp = self._generate_for(sim, now)
                bucket.append(opp)
                engine._bus.emit(
                    "opportunity_generated", sim=sim, opportunity=opp, tick=now
                )

    def _generate_for(self, sim, now: int) -> Opportunity:
        focus = random.choice(["social", "career", "skill", "romance", "exploration"])
        req = {
            "need_social_min": 20.0,
            "emotion_not": ["furious"],
        }
        rewards = {
            "simoleons": random.randint(40, 160),
            "reputation": random.uniform(0.5, 2.0),
        }
        return Opportunity(
            opportunity_id=f"opp_{sim.sim_id}_{now}_{random.randint(100, 999)}",
            trigger=focus,
            requirements=req,
            rewards=rewards,
            expires_tick=now + random.randint(8, 24),
        )

    def for_sim(self, sim_id: str) -> list[dict]:
        return [
            {
                "id": o.opportunity_id,
                "trigger": o.trigger,
                "requirements": dict(o.requirements),
                "rewards": dict(o.rewards),
                "expires_tick": o.expires_tick,
                "status": o.status,
                "metadata": dict(o.metadata),
            }
            for o in self._active.get(sim_id, [])
        ]
