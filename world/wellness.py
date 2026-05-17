from __future__ import annotations

import random


MEDITATION_STATES = ["relaxed", "focused", "deep_trance", "levitating", "transcendent"]


class WellnessSystem:
    def __init__(self) -> None:
        self._state: dict[str, dict] = {}

    def tick(self, engine) -> None:
        for sim in engine.sims:
            ws = self._state.setdefault(
                sim.sim_id,
                {
                    "stress_level": 35.0,
                    "calmness": 45.0,
                    "emotional_resistance": 0.0,
                    "recovery_rate": 1.0,
                    "meditation_state": "relaxed",
                    "focus_modifier": 1.0,
                    "teleport_cooldown": 0,
                },
            )
            self._passive_regulation(sim, ws)
            self._autonomous_mindfulness(sim, ws, engine)
            self._massage_and_sauna(sim, ws)
            self._advanced_abilities(sim, ws)

    def _passive_regulation(self, sim, ws: dict) -> None:
        wellness = sim.skills.levels.get("wellness", 0.0)
        burnout = 10.0 if getattr(sim, "_burnout_active", False) else 0.0
        pressure = max(0.0, (60.0 - sim.needs.energy) * 0.25 + burnout)
        ws["stress_level"] = max(
            0.0,
            min(
                100.0, ws["stress_level"] + pressure * 0.02 - (0.25 + wellness * 0.035)
            ),
        )
        ws["calmness"] = max(
            0.0,
            min(100.0, ws["calmness"] + (wellness * 0.05) - ws["stress_level"] * 0.01),
        )
        ws["emotional_resistance"] = max(
            0.0, min(1.0, 0.1 + wellness / 15.0 + ws["calmness"] / 250.0)
        )
        ws["recovery_rate"] = max(0.7, min(1.8, 0.9 + wellness * 0.03))
        ws["focus_modifier"] = max(
            0.7, min(1.6, 0.9 + ws["calmness"] / 120.0 - ws["stress_level"] / 200.0)
        )
        sim.wellness_state = dict(ws)

    def _autonomous_mindfulness(self, sim, ws: dict, engine) -> None:
        wellness = sim.skills.levels.get("wellness", 0.0)
        calm_trait_bonus = 0.12 if "calm" in sim.profile.get("traits", []) else 0.0
        intro_bonus = 0.08 if "loner" in sim.profile.get("traits", []) else 0.0
        utility = ws["stress_level"] / 100.0 + calm_trait_bonus + intro_bonus
        if utility < 0.55:
            return
        activity = random.choice(
            ["meditation", "yoga", "breathing_exercises", "mindfulness"]
        )
        if activity == "meditation":
            ws["stress_level"] = max(0.0, ws["stress_level"] - (5.0 + wellness * 0.9))
            ws["calmness"] = min(100.0, ws["calmness"] + (4.0 + wellness * 0.8))
            sim.skills.gain_xp("wellness", 0.16)
            sim.emotion.add("focus", 0.35, duration=3, source="meditation")
            sim.needs.energy = min(100.0, sim.needs.energy + 1.4)
            idx = min(4, int(wellness // 2))
            ws["meditation_state"] = MEDITATION_STATES[idx]
        elif activity == "yoga":
            ws["stress_level"] = max(0.0, ws["stress_level"] - (3.0 + wellness * 0.7))
            sim.skills.gain_xp("wellness", 0.12)
            sim.skills.gain_xp("fitness", 0.08)
            sim.emotion.add("optimism", 0.25, duration=2, source="yoga")
            sim.needs.energy = max(0.0, sim.needs.energy - 0.8)
        elif activity == "breathing_exercises":
            ws["stress_level"] = max(0.0, ws["stress_level"] - (2.0 + wellness * 0.5))
            sim.emotion.add("relief", 0.2, duration=2, source="breathing")
        else:
            ws["stress_level"] = max(0.0, ws["stress_level"] - (2.5 + wellness * 0.6))
            sim.emotion.add("focus", 0.2, duration=2, source="mindfulness")

        # Burnout prevention hook
        if getattr(sim, "_burnout_active", False) and ws["stress_level"] < 30:
            sim._burnout_recovery_ticks += 1

        # Shared wellness relationship bonus
        if random.random() < 0.08:
            others = [o for o in engine.sims if o.sim_id != sim.sim_id]
            if others:
                peer = random.choice(others)
                rel = engine.relationships.get(sim.sim_id, peer.sim_id)
                rel.apply_deltas(0.4 + wellness * 0.03, 0.0)

    def _massage_and_sauna(self, sim, ws: dict) -> None:
        wellness = sim.skills.levels.get("wellness", 0.0)
        if wellness >= 3 and random.random() < 0.04:
            massage_type = random.choice(
                ["aromatherapy", "deep_tissue", "sports", "stone", "fertility"]
            )
            boost = 2.5 + wellness * 0.5
            ws["stress_level"] = max(0.0, ws["stress_level"] - boost)
            ws["calmness"] = min(100.0, ws["calmness"] + boost)
            sim.emotion.add(
                "relief", 0.35, duration=3, source=f"massage:{massage_type}"
            )
            if massage_type == "fertility":
                sim.emotion.add("hope", 0.25, duration=2, source="fertility_massage")
        if random.random() < 0.03:
            ws["stress_level"] = max(0.0, ws["stress_level"] - 1.8)
            sim.emotion.add("comfort", 0.2, duration=2, source="sauna")

    def _advanced_abilities(self, sim, ws: dict) -> None:
        wellness = sim.skills.levels.get("wellness", 0.0)
        if ws["teleport_cooldown"] > 0:
            ws["teleport_cooldown"] -= 1
        if wellness >= 8 and ws["calmness"] > 75 and random.random() < 0.03:
            ws["meditation_state"] = "levitating"
            sim.emotion.add("awe", 0.45, duration=3, source="levitation")
        if (
            wellness >= 10
            and ws["focus_modifier"] > 1.3
            and ws["teleport_cooldown"] <= 0
            and random.random() < 0.015
        ):
            ws["teleport_cooldown"] = 8
            sim.needs.energy = max(0.0, sim.needs.energy - 4.0)
            sim.emotion.add("focus", 0.5, duration=2, source="teleportation")

    def state_for(self, sim_id: str) -> dict:
        return dict(self._state.get(sim_id, {}))
