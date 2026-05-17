from __future__ import annotations

from dataclasses import dataclass, field
import random


@dataclass
class LifeState:
    id: str
    category: str
    appearance_modifiers: dict = field(default_factory=dict)
    abilities: dict = field(default_factory=dict)
    weaknesses: dict = field(default_factory=dict)
    needs_override: dict = field(default_factory=dict)
    autonomy_modifiers: dict = field(default_factory=dict)
    transformation_rules: dict = field(default_factory=dict)


LIFE_STATES: dict[str, LifeState] = {
    "human": LifeState("human", "humanoid"),
    "alien": LifeState(
        "alien",
        "supernatural",
        abilities={"telepathy": {"cooldown": 6, "energy_cost": 2.0}},
        autonomy_modifiers={"learning": 0.12},
    ),
    "vampire": LifeState(
        "vampire",
        "supernatural",
        abilities={
            "mind_control": {"cooldown": 8, "energy_cost": 4.0},
            "teleport": {"cooldown": 10, "energy_cost": 5.0},
        },
        weaknesses={"sunlight_penalty": 0.25},
        needs_override={"hunger_to_plasma": True},
        autonomy_modifiers={"romance": 0.05, "social": 0.08},
        transformation_rules={"infection": 0.05},
    ),
    "werewolf": LifeState(
        "werewolf",
        "supernatural",
        abilities={"hunt": {"cooldown": 7, "energy_cost": 3.0}},
        weaknesses={"rage": 0.22},
        autonomy_modifiers={"conflict": 0.1, "outdoors": 0.12},
        transformation_rules={"environmental_exposure": 0.04},
    ),
    "witch": LifeState(
        "witch",
        "supernatural",
        abilities={
            "curse": {"cooldown": 10, "energy_cost": 4.5},
            "healing": {"cooldown": 7, "energy_cost": 3.0},
        },
        needs_override={"energy_to_magic": True},
        autonomy_modifiers={"learning": 0.12},
    ),
    "fairy": LifeState(
        "fairy",
        "nature",
        abilities={
            "flight": {"cooldown": 3, "energy_cost": 1.5},
            "blessing": {"cooldown": 9, "energy_cost": 3.5},
        },
        autonomy_modifiers={"harmony": 0.14, "outdoors": 0.08},
    ),
    "ghost": LifeState(
        "ghost",
        "undead",
        abilities={
            "haunt": {"cooldown": 5, "energy_cost": 0.0},
            "possess": {"cooldown": 9, "energy_cost": 1.0},
        },
        weaknesses={},
        needs_override={"suppress_hunger": True},
        autonomy_modifiers={"social": -0.04, "conflict": 0.06},
    ),
    "mermaid": LifeState(
        "mermaid",
        "nature",
        abilities={"aquatic_speed": {"cooldown": 0, "energy_cost": 0.0}},
        weaknesses={"dehydration": 0.2},
        autonomy_modifiers={"outdoors": 0.1},
    ),
    "plant_based": LifeState(
        "plant_based",
        "nature",
        abilities={"photosynthesis": {"cooldown": 4, "energy_cost": -1.5}},
        needs_override={"food_to_sunlight": True},
        autonomy_modifiers={"outdoors": 0.14, "harmony": 0.08},
    ),
    "robotic": LifeState(
        "robotic",
        "artificial",
        abilities={"self_repair": {"cooldown": 10, "energy_cost": 0.0}},
        weaknesses={"maintenance": 0.2},
        needs_override={"battery": True},
        autonomy_modifiers={"learning": 0.15, "career_focus": 0.1},
    ),
}


class LifeStateSystem:
    def __init__(self) -> None:
        self.hidden_identity: dict[str, dict] = {}
        self.occult_progression: dict[str, dict] = {}
        self.hybrid_data: dict[str, dict] = {}

    def tick(self, engine) -> None:
        for sim in engine.sims:
            self._bootstrap(sim)
            self._apply_need_overrides(sim)
            self._apply_autonomy(sim)
            self._run_abilities(sim)
            self._run_transformations(sim)
            self._social_reaction(sim, engine)
            self._progression(sim)

    def _bootstrap(self, sim) -> None:
        lid = self._life_state_id(sim)
        if not hasattr(sim, "life_state"):
            sim.life_state = lid
        if sim.sim_id not in self.hidden_identity:
            self.hidden_identity[sim.sim_id] = {
                "disguise": lid in {"alien", "vampire", "witch"},
                "exposure_risk": 0.0,
                "secrecy_level": 1.0 if lid in {"alien", "vampire", "witch"} else 0.2,
            }
        if sim.sim_id not in self.occult_progression:
            self.occult_progression[sim.sim_id] = {
                "rank": 0,
                "powers": [],
                "weaknesses": [],
                "mastery_unlocks": [],
            }

    def _life_state_id(self, sim) -> str:
        if getattr(sim, "is_ghost", False):
            return "ghost"
        occ = str(getattr(sim, "occult_type", "none") or "none")
        if occ in LIFE_STATES:
            return occ
        if occ == "robot":
            return "robotic"
        return "human"

    def _apply_need_overrides(self, sim) -> None:
        st = LIFE_STATES.get(self._life_state_id(sim), LIFE_STATES["human"])
        no = st.needs_override
        if no.get("hunger_to_plasma"):
            sim.needs.hunger = max(0.0, sim.needs.hunger - 0.4)
            sim.occult_power = min(100.0, sim.occult_power + 0.3)
        if no.get("food_to_sunlight"):
            sim.needs.hunger = min(100.0, sim.needs.hunger + 0.25)
        if no.get("energy_to_magic"):
            sim.occult_power = min(
                100.0, sim.occult_power + max(0.0, (sim.needs.energy - 40.0) * 0.01)
            )
        if no.get("battery"):
            sim.needs.energy = max(0.0, sim.needs.energy - 0.15)
        if no.get("suppress_hunger"):
            sim.needs.hunger = max(sim.needs.hunger, 55.0)

    def _apply_autonomy(self, sim) -> None:
        st = LIFE_STATES.get(self._life_state_id(sim), LIFE_STATES["human"])
        prof = dict(getattr(sim, "autonomy_profile", {}))
        for k, v in st.autonomy_modifiers.items():
            prof[k] = max(-1.0, min(1.0, prof.get(k, 0.0) + v))
        sim.autonomy_profile = prof

    def _run_abilities(self, sim) -> None:
        st = LIFE_STATES.get(self._life_state_id(sim), LIFE_STATES["human"])
        for ability, cfg in st.abilities.items():
            if random.random() > 0.015:
                continue
            cost = float(cfg.get("energy_cost", 0.0))
            sim.needs.energy = max(0.0, min(100.0, sim.needs.energy - cost))
            if ability in {"healing", "blessing", "photosynthesis"}:
                sim.emotion.add(
                    "optimism", 0.25, duration=2, source=f"ability:{ability}"
                )
            elif ability in {"haunt", "curse", "mind_control", "possess"}:
                sim.emotion.add("focus", 0.2, duration=2, source=f"ability:{ability}")

    def _run_transformations(self, sim) -> None:
        st = LIFE_STATES.get(self._life_state_id(sim), LIFE_STATES["human"])
        rules = st.transformation_rules
        if rules and random.random() < max(rules.values(), default=0.0) * 0.02:
            temporary = random.choice(
                ["spectral", "energized", "enchanted", "cursed", "zombified"]
            )
            sim.temporary_traits.add(temporary)
            if not hasattr(sim, "temporary_state"):
                sim.temporary_state = {}
            sim.temporary_state = {
                "id": temporary,
                "duration": random.randint(3, 8),
                "trigger": random.choice(
                    ["infection", "curse", "potion", "ritual", "environmental_exposure"]
                ),
                "restrictions": ["outfit_changes"]
                if temporary in {"zombified", "spectral"}
                else [],
            }

    def _social_reaction(self, sim, engine) -> None:
        sid = self._life_state_id(sim)
        if sid == "human":
            return
        for other in random.sample(engine.sims, k=min(3, len(engine.sims))):
            if other.sim_id == sim.sim_id:
                continue
            rel = engine.relationships.get(sim.sim_id, other.sim_id)
            delta = 0.0
            if sid in {"vampire", "werewolf", "ghost", "mummy"}:
                delta -= 0.2
            if sid in {"fairy", "alien", "witch", "mermaid"}:
                delta += 0.1
            rel.apply_deltas(delta, 0.0)

    def _progression(self, sim) -> None:
        sid = self._life_state_id(sim)
        if sid == "human":
            return
        rec = self.occult_progression[sim.sim_id]
        rec["rank"] = min(10, rec["rank"] + (1 if random.random() < 0.02 else 0))
        if rec["rank"] >= 3 and "minor_power" not in rec["mastery_unlocks"]:
            rec["mastery_unlocks"].append("minor_power")
        if rec["rank"] >= 7 and "major_power" not in rec["mastery_unlocks"]:
            rec["mastery_unlocks"].append("major_power")

    def state_for(self, sim) -> dict:
        sid = self._life_state_id(sim)
        return {
            "life_state": sid,
            "category": LIFE_STATES.get(sid, LIFE_STATES["human"]).category,
            "abilities": list(
                LIFE_STATES.get(sid, LIFE_STATES["human"]).abilities.keys()
            ),
            "weaknesses": dict(LIFE_STATES.get(sid, LIFE_STATES["human"]).weaknesses),
            "needs_override": dict(
                LIFE_STATES.get(sid, LIFE_STATES["human"]).needs_override
            ),
            "hidden_identity": dict(self.hidden_identity.get(sim.sim_id, {})),
            "occult_progression": dict(self.occult_progression.get(sim.sim_id, {})),
            "temporary_state": dict(getattr(sim, "temporary_state", {})),
            "hybrid": dict(self.hybrid_data.get(sim.sim_id, {})),
        }

    def register_hybrid_offspring(self, child, parent_a, parent_b) -> None:
        sa = self._life_state_id(parent_a)
        sb = self._life_state_id(parent_b)
        if sa == sb == "human":
            return
        inherited = sorted({sa, sb} - {"human"})
        self.hybrid_data[child.sim_id] = {
            "parent_species": [sa, sb],
            "inherited_abilities": inherited,
            "instability": round(random.uniform(0.0, 0.4), 3),
        }
