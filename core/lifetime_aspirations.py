from __future__ import annotations

from dataclasses import dataclass, field
import random


ASPIRATION_CATEGORIES = {
    "Fortune": "wealth",
    "Family": "family",
    "Popularity": "social",
    "Knowledge": "knowledge",
    "Romance": "romance",
    "Creative": "creativity",
    "Athletics": "athletics",
    "Fame": "fame",
    "Crime": "crime",
    "Exploration": "exploration",
    "Survival": "survival",
    "Supernatural": "supernatural",
    "Animals": "animals",
    "Career": "career",
}

TRAIT_ASPIRATION_WEIGHTS = {
    "ambitious": {"Fortune": 0.35, "Career": 0.35, "Fame": 0.2},
    "romantic": {"Romance": 0.45, "Family": 0.2, "Popularity": 0.1},
    "creative": {"Creative": 0.45, "Fame": 0.2},
    "genius": {"Knowledge": 0.5, "Career": 0.15},
    "geek": {"Knowledge": 0.35, "Career": 0.2},
    "active": {"Athletics": 0.45, "Exploration": 0.15},
    "evil": {"Crime": 0.5, "Fame": 0.1},
    "family-oriented": {"Family": 0.5, "Romance": 0.15},
    "loner": {"Knowledge": 0.2, "Creative": 0.25, "Popularity": -0.3},
}


@dataclass
class LifetimeAspiration:
    id: str
    category: str
    requirements: dict
    progress: float = 0.0
    rewards: dict = field(default_factory=dict)
    trait_synergies: list[str] = field(default_factory=list)
    completion_state: bool = False
    difficulty: str = "medium"


@dataclass
class AspirationFulfillment:
    life_satisfaction: float = 50.0
    aligned_traits_bonus: float = 0.0
    failed_goals_penalty: float = 0.0
    abandoned_goal_penalty: float = 0.0


class AspirationSystem:
    def __init__(self) -> None:
        self.legacy: dict[str, dict] = {}

    def bootstrap(self, sim) -> None:
        if not hasattr(sim, "lifetime_aspiration"):
            asp_id = sim.profile.get("aspiration", "Fortune")
            sim.lifetime_aspiration = LifetimeAspiration(
                id=asp_id,
                category=ASPIRATION_CATEGORIES.get(asp_id, "career"),
                requirements={},
                rewards={"reputation": 2.0, "fulfillment": 8.0},
                trait_synergies=list(sim.profile.get("traits", [])),
            )
        if not hasattr(sim, "aspiration_fulfillment"):
            sim.aspiration_fulfillment = AspirationFulfillment()
        if not hasattr(sim, "aspiration_discoveries"):
            sim.aspiration_discoveries = []
        if not hasattr(sim, "completed_aspirations"):
            sim.completed_aspirations = []

    def tick(self, sim, engine, current_tick: int) -> None:
        self.bootstrap(sim)
        if sim.lifetime_aspiration.id == "Knowledge":
            from core.knowledge_aspiration import apply_knowledge_tick

            apply_knowledge_tick(sim, engine, current_tick)
        self._dynamic_discovery(sim, engine)
        self._autonomy_bias(sim)
        self._fulfillment_update(sim)
        self._legacy_update(sim)
        self._social_recognition(sim)
        self._childhood_influence(sim, engine)
        self._procedural_generation_hook(sim)
        self._completion_check(sim, current_tick)

    def _dynamic_discovery(self, sim, engine) -> None:
        discovered = set(getattr(sim, "aspiration_discoveries", []))
        levels = sim.skills.levels
        if levels.get("fitness", 0) >= 4:
            discovered.add("Athletics")
        if levels.get("programming", 0) >= 4 or levels.get("logic", 0) >= 5:
            discovered.add("Knowledge")
        if levels.get("painting", 0) >= 4 or levels.get("writing", 0) >= 4:
            discovered.add("Creative")
        if sim.hacker_reputation > 5:
            discovered.add("Crime")
        if getattr(sim, "celebrity_score", 0.0) > 30:
            discovered.add("Fame")
        if len(getattr(sim, "travel_history", [])) >= 3:
            discovered.add("Exploration")
        sim.aspiration_discoveries = sorted(discovered)

    def _autonomy_bias(self, sim) -> None:
        asp = sim.lifetime_aspiration.id
        prof = dict(getattr(sim, "autonomy_profile", {}))
        if asp == "Fortune":
            prof["career_focus"] = min(1.0, prof.get("career_focus", 0.0) + 0.1)
        elif asp == "Romance":
            prof["romance"] = min(1.0, prof.get("romance", 0.0) + 0.12)
        elif asp == "Popularity":
            prof["social"] = min(1.0, prof.get("social", 0.0) + 0.1)
        elif asp == "Knowledge":
            prof["learning"] = min(1.0, prof.get("learning", 0.0) + 0.12)
        elif asp == "Athletics":
            prof["outdoors"] = min(1.0, prof.get("outdoors", 0.0) + 0.08)
        elif asp == "Creative":
            prof["learning"] = min(1.0, prof.get("learning", 0.0) + 0.08)
            prof["leisure"] = min(1.0, prof.get("leisure", 0.0) + 0.08)
        sim.autonomy_profile = prof

    def _fulfillment_update(self, sim) -> None:
        f = sim.aspiration_fulfillment
        asp = sim.lifetime_aspiration
        trait_bonus = 0.0
        for t in sim.profile.get("traits", []):
            trait_bonus += TRAIT_ASPIRATION_WEIGHTS.get(t, {}).get(asp.id, 0.0)
        f.aligned_traits_bonus = max(0.0, trait_bonus)
        target = 40.0 + asp.progress * 50.0 + f.aligned_traits_bonus * 20.0
        f.life_satisfaction += (target - f.life_satisfaction) * 0.05
        f.life_satisfaction = max(
            0.0,
            min(
                100.0,
                f.life_satisfaction - f.abandoned_goal_penalty - f.failed_goals_penalty,
            ),
        )

    def _legacy_update(self, sim) -> None:
        self.legacy[sim.sim_id] = {
            "completed_aspirations": list(getattr(sim, "completed_aspirations", [])),
            "family_reputation": round(getattr(sim, "reputation_score", 0.0), 2),
            "inherited_traits": list(getattr(sim, "reward_traits", set())),
        }

    def _social_recognition(self, sim) -> None:
        asp = sim.lifetime_aspiration.id
        if asp == "Fame" and sim.lifetime_aspiration.progress > 0.6:
            sim.reputation_score = min(100.0, sim.reputation_score + 0.03)
        if asp == "Career" and sim.career_level >= 6:
            sim.reputation_score = min(100.0, sim.reputation_score + 0.02)

    def _childhood_influence(self, sim, engine) -> None:
        age = int(sim.profile.get("age", 25))
        if age > 17:
            return
        parent_ids = sim.profile.get("parent_ids", [])
        if not parent_ids:
            return
        parents = [
            engine._sim_lookup.get(pid)
            for pid in parent_ids
            if pid in engine._sim_lookup
        ]
        if any(p and p.career_level >= 5 for p in parents):
            if "Career" not in sim.aspiration_discoveries:
                sim.aspiration_discoveries.append("Career")

    def _procedural_generation_hook(self, sim) -> None:
        if not hasattr(sim, "generated_aspirations"):
            sim.generated_aspirations = []
        if random.random() < 0.01 and len(sim.generated_aspirations) < 3:
            skills = sorted(
                sim.skills.levels.items(), key=lambda kv: kv[1], reverse=True
            )
            if skills:
                top_skill, level = skills[0]
                sim.generated_aspirations.append(
                    {
                        "id": f"generated_{top_skill}_{len(sim.generated_aspirations) + 1}",
                        "generated_goals": [
                            f"Reach {top_skill} level {min(10, int(level) + 2)}"
                        ],
                        "contextual_requirements": {"skill": top_skill},
                        "trait_bias": list(sim.profile.get("traits", []))[:2],
                    }
                )

    def _completion_check(self, sim, current_tick: int) -> None:
        asp = sim.lifetime_aspiration
        if asp.completion_state:
            return
        if asp.progress >= 1.0:
            asp.completion_state = True
            sim.completed_aspirations.append(asp.id)
            sim.reward_traits.add("emotional_resilience")
            _eng = getattr(sim, '_engine_ref', None)
            if _eng:
                from persistence.ledger import TX_LIFETIME_REWARD
                _eng._tx(sim, 1000.0, TX_LIFETIME_REWARD, description='aspiration reward')
            else:
                sim.simoleons += 1000.0
            sim.reputation_score = min(100.0, sim.reputation_score + 2.0)
            sim.emotion.add(
                "pride", 0.9, duration=12, source=f"aspiration_complete:{asp.id}"
            )
            sim.milestones.append(
                {"id": f"aspiration_complete:{asp.id}", "tick": current_tick}
            )

    def update_progress_from_wish(self, sim, wish_progress: float) -> None:
        self.bootstrap(sim)
        sim.lifetime_aspiration.progress = max(0.0, min(1.0, wish_progress))

    def abandonment_penalty(self, sim, new_aspiration: str) -> None:
        self.bootstrap(sim)
        f = sim.aspiration_fulfillment
        f.abandoned_goal_penalty = min(10.0, f.abandoned_goal_penalty + 1.5)
        sim.lifetime_aspiration = LifetimeAspiration(
            id=new_aspiration,
            category=ASPIRATION_CATEGORIES.get(new_aspiration, "career"),
            requirements={},
            rewards={"reputation": 2.0, "fulfillment": 8.0},
            trait_synergies=list(sim.profile.get("traits", [])),
        )
