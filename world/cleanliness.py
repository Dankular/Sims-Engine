from __future__ import annotations

from dataclasses import dataclass, field
import random


@dataclass
class DirtState:
    cleanliness: float = 100.0
    decay_rate: float = 0.3
    contamination_level: float = 0.0
    dirt_sources: dict[str, float] = field(default_factory=dict)


@dataclass
class RoomCleanliness:
    room_id: str
    cleanliness_score: float = 100.0
    odor_level: float = 0.0
    dust_level: float = 0.0
    contamination_sources: dict[str, float] = field(default_factory=dict)


class CleanlinessSystem:
    def __init__(self) -> None:
        self.objects: dict[str, DirtState] = {}
        self.rooms: dict[str, RoomCleanliness] = {}
        self.puddles: dict[str, dict] = {}

    def tick(self, engine) -> None:
        for sim in engine.sims:
            self._sim_dirt_accumulation(sim)
            self._apply_room_effects(sim)
            self._autonomous_cleaning(sim)
        self._evaporate_puddles()

    def _sim_dirt_accumulation(self, sim) -> None:
        room_id = sim.household_id or "public"
        room = self.rooms.setdefault(room_id, RoomCleanliness(room_id=room_id))
        usage = 0.2 + random.uniform(0.0, 0.6)
        room.cleanliness_score = max(0.0, room.cleanliness_score - usage)
        room.dust_level = min(100.0, room.dust_level + 0.08)
        room.odor_level = min(
            100.0, room.odor_level + (100.0 - room.cleanliness_score) * 0.003
        )
        if random.random() < 0.02:
            self.puddles[f"{room_id}:{random.randint(1, 9999)}"] = {
                "size": random.uniform(5.0, 20.0),
                "evaporation_rate": random.uniform(0.3, 1.0),
                "slip_risk": random.uniform(0.05, 0.2),
                "contamination": random.uniform(1.0, 6.0),
                "room_id": room_id,
            }

    def _apply_room_effects(self, sim) -> None:
        room = self.rooms.setdefault(
            sim.household_id or "public",
            RoomCleanliness(room_id=sim.household_id or "public"),
        )
        c = room.cleanliness_score
        if c >= 85:
            sim.emotion.add("optimism", 0.15, duration=2, source="clean_room")
        elif c >= 55:
            pass
        elif c >= 30:
            sim.emotion.add("discomfort", 0.2, duration=2, source="dirty_room")
            sim.needs.environment = max(0.0, sim.needs.environment - 0.8)
        else:
            sim.emotion.add("annoyance", 0.3, duration=3, source="filthy_room")
            sim.needs.environment = max(0.0, sim.needs.environment - 1.5)
            sim.reputation_score = max(-100.0, sim.reputation_score - 0.05)

    def _autonomous_cleaning(self, sim) -> None:
        room = self.rooms.setdefault(
            sim.household_id or "public",
            RoomCleanliness(room_id=sim.household_id or "public"),
        )
        neat = "neat" in sim.profile.get("traits", [])
        slob = "slob" in sim.profile.get("traits", [])
        utility = (100.0 - room.cleanliness_score) / 100.0
        if neat:
            utility += 0.25
        if slob:
            utility -= 0.2
        utility += float(getattr(sim, "autonomy_profile", {}).get("harmony", 0.0)) * 0.1
        if utility > 0.65 and sim.needs.energy > 20:
            self._perform_cleaning(sim, room)

    def _perform_cleaning(self, sim, room: RoomCleanliness) -> None:
        skill = sim.skills.levels.get("cleaning", 0.0)
        efficiency = 4.0 + skill * 0.8
        room.cleanliness_score = min(100.0, room.cleanliness_score + efficiency)
        room.dust_level = max(0.0, room.dust_level - efficiency * 0.8)
        room.odor_level = max(0.0, room.odor_level - efficiency * 0.6)
        sim.needs.energy = max(0.0, sim.needs.energy - 1.2)
        sim.skills.gain_xp("cleaning", 0.14)
        sim.emotion.add("relief", 0.2, duration=2, source="cleaning")

    def _evaporate_puddles(self) -> None:
        dead = []
        for pid, puddle in self.puddles.items():
            puddle["size"] = max(0.0, puddle["size"] - puddle["evaporation_rate"])
            if puddle["size"] <= 0.0:
                dead.append(pid)
        for pid in dead:
            self.puddles.pop(pid, None)

    def room_state(self) -> list[dict]:
        return [
            {
                "room_id": r.room_id,
                "cleanliness_score": round(r.cleanliness_score, 2),
                "odor_level": round(r.odor_level, 2),
                "dust_level": round(r.dust_level, 2),
            }
            for r in self.rooms.values()
        ]
