"""
world/factions.py — Organic faction formation from shared beliefs.

Sims that share high-confidence beliefs cluster into Factions that pursue
collective goals (strikes, campaigns, propaganda). Factions accumulate
resources, gain influence, and clash with ideological rivals.

FactionManager.tick(engine) is called every tick from SimEngine.run_tick().
"""
from __future__ import annotations

import logging
import random
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)

__all__ = ["Faction", "FactionEvent", "FactionManager"]


@dataclass
class Faction:
    faction_id: str
    name: str
    ideology: dict[str, float]
    member_ids: list[str]
    leader_id: str
    resources: float
    influence: float
    goals: list[str]
    age_ticks: int
    rival_faction_ids: list[str]
    collective_action: str
    collective_action_ticks: int


@dataclass
class FactionEvent:
    tick: int
    faction_id: str
    event_type: str
    description: str


class FactionManager:
    MIN_FACTION_SIZE = 3
    FORMATION_INTERVAL = 25
    BELIEF_ALIGNMENT_THRESHOLD = 0.4
    SIMILARITY_THRESHOLD = 0.5

    def __init__(self) -> None:
        self.factions: list[Faction] = []
        self.events: list[FactionEvent] = []

    # ── Public tick ───────────────────────────────────────────────────────────

    def tick(self, engine: "SimEngine") -> None:
        if engine.tick_count % self.FORMATION_INTERVAL == 0:
            self._check_formation(engine)

        for faction in list(self.factions):
            self._tick_faction(faction, engine)

        if engine.tick_count % 15 == 0:
            self._check_rivals(engine)

    # ── Formation ─────────────────────────────────────────────────────────────

    def _check_formation(self, engine: "SimEngine") -> None:
        sim_beliefs: dict[str, set[tuple[str, str]]] = {}
        for sim in engine.sims:
            bg = getattr(sim, "beliefs", None)
            if bg is None:
                continue
            confident = bg.confident_beliefs(self.BELIEF_ALIGNMENT_THRESHOLD)
            sim_beliefs[sim.sim_id] = {
                (n.predicate, n.object_) for n in confident
            }

        if len(sim_beliefs) < self.MIN_FACTION_SIZE:
            return

        existing_members: set[str] = {
            mid for f in self.factions for mid in f.member_ids
        }

        sim_ids = list(sim_beliefs.keys())
        used: set[str] = set()

        for i, sid_a in enumerate(sim_ids):
            if sid_a in used or sid_a in existing_members:
                continue
            cluster = [sid_a]
            beliefs_a = sim_beliefs[sid_a]
            if not beliefs_a:
                continue
            for sid_b in sim_ids[i + 1:]:
                if sid_b in used or sid_b in existing_members:
                    continue
                beliefs_b = sim_beliefs[sid_b]
                if not beliefs_b:
                    continue
                union = beliefs_a | beliefs_b
                if len(union) == 0:
                    continue
                shared = beliefs_a & beliefs_b
                similarity = len(shared) / max(len(beliefs_a), len(beliefs_b), 1)
                if similarity >= self.SIMILARITY_THRESHOLD:
                    cluster.append(sid_b)

            if len(cluster) < self.MIN_FACTION_SIZE:
                continue

            ideology = self._build_ideology(cluster, sim_beliefs)
            goals = self._derive_goals(ideology)
            leader_id = self._elect_leader(cluster, engine)
            ideology_label = max(ideology, key=ideology.get) if ideology else "mixed"
            faction_id = str(uuid.uuid4())[:8]
            name = f"{ideology_label}_{len(cluster)}_bloc"

            faction = Faction(
                faction_id=faction_id,
                name=name,
                ideology=ideology,
                member_ids=list(cluster),
                leader_id=leader_id,
                resources=0.0,
                influence=0.1,
                goals=goals,
                age_ticks=0,
                rival_faction_ids=[],
                collective_action="",
                collective_action_ticks=0,
            )
            self.factions.append(faction)
            used.update(cluster)

            self._record_event(
                engine.tick_count, faction_id, "formed",
                f"Faction '{name}' formed around ideology: "
                + ", ".join(f"{k}={v:.2f}" for k, v in list(ideology.items())[:3]),
            )
            engine._bus.emit(
                "faction_formed",
                faction_id=faction_id,
                name=name,
                member_count=len(cluster),
                tick=engine.tick_count,
            )
            logger.debug("[Factions] formed '%s' (%d members)", name, len(cluster))

    def _build_ideology(
        self,
        cluster: list[str],
        sim_beliefs: dict[str, set[tuple[str, str]]],
    ) -> dict[str, float]:
        predicate_counts: dict[str, int] = {}
        for sid in cluster:
            for pred, _ in sim_beliefs.get(sid, set()):
                predicate_counts[pred] = predicate_counts.get(pred, 0) + 1
        n = len(cluster)
        return {
            pred: round(count / n, 3)
            for pred, count in predicate_counts.items()
            if count / n >= self.BELIEF_ALIGNMENT_THRESHOLD
        }

    def _derive_goals(self, ideology: dict[str, float]) -> list[str]:
        goals: list[str] = []
        if ideology.get("is_corrupt", 0.0) >= 0.5:
            goals.append("oust_leader")
        if ideology.get("earns_less", 0.0) >= 0.5:
            goals.append("form_union")
        if ideology.get("owns_property", 0.0) >= 0.5:
            goals.append("control_economy")
        if not goals:
            goals.append("gain_influence")
        return goals

    def _elect_leader(self, cluster: list[str], engine: "SimEngine") -> str:
        sim_map = {s.sim_id: s for s in engine.sims}
        best = max(
            cluster,
            key=lambda sid: getattr(sim_map.get(sid), "reputation_score", 0.0),
        )
        return best

    # ── Per-faction tick ──────────────────────────────────────────────────────

    def _tick_faction(self, faction: Faction, engine: "SimEngine") -> None:
        faction.age_ticks += 1
        faction.resources += len(faction.member_ids) * 0.5
        faction.influence = max(0.0, faction.influence - 0.001)

        if faction.collective_action_ticks > 0:
            faction.collective_action_ticks -= 1
            if faction.collective_action_ticks == 0:
                self._end_action(faction, engine)
        elif faction.influence > 0.4 and faction.goals:
            self._start_action(faction, engine)

        if len(faction.member_ids) < 2:
            self._dissolve(faction, engine)

    def _start_action(self, faction: Faction, engine: "SimEngine") -> None:
        goal = faction.goals[0]
        sim_map = {s.sim_id: s for s in engine.sims}

        if goal == "oust_leader":
            faction.collective_action = "campaign"
            faction.collective_action_ticks = 20
            for sid in faction.member_ids:
                sim = sim_map.get(sid)
                if sim is not None:
                    sim.reputation_score = min(100.0, sim.reputation_score + 2.0)
        elif goal == "form_union":
            faction.collective_action = "strike"
            faction.collective_action_ticks = 15
            for sid in faction.member_ids:
                sim = sim_map.get(sid)
                if sim is not None:
                    sim.career_performance = min(100.0, sim.career_performance + 3.0)
        elif goal == "control_economy":
            faction.collective_action = "propaganda"
            faction.collective_action_ticks = 25
            for sid in faction.member_ids:
                sim = sim_map.get(sid)
                if sim is not None and hasattr(sim, "moodlets"):
                    sim.moodlets.add("inspired", source=f"faction_{faction.faction_id}")
        else:
            faction.collective_action = "propaganda"
            faction.collective_action_ticks = 10

        self._record_event(
            engine.tick_count, faction.faction_id, "action_started",
            f"Faction '{faction.name}' started '{faction.collective_action}' "
            f"(goal: {goal})",
        )
        engine._bus.emit(
            "faction_action_started",
            faction_id=faction.faction_id,
            action=faction.collective_action,
            goal=goal,
            tick=engine.tick_count,
        )
        logger.debug(
            "[Factions] '%s' started action '%s'",
            faction.name, faction.collective_action,
        )

    def _end_action(self, faction: Faction, engine: "SimEngine") -> None:
        engine._bus.emit(
            "faction_action_ended",
            faction_id=faction.faction_id,
            action=faction.collective_action,
            tick=engine.tick_count,
        )
        faction.influence = min(1.0, faction.influence + 0.1)
        faction.collective_action = ""

    def _dissolve(self, faction: Faction, engine: "SimEngine") -> None:
        self._record_event(
            engine.tick_count, faction.faction_id, "dissolved",
            f"Faction '{faction.name}' dissolved after {faction.age_ticks} ticks",
        )
        engine._bus.emit(
            "faction_dissolved",
            faction_id=faction.faction_id,
            tick=engine.tick_count,
        )
        logger.debug("[Factions] dissolved '%s'", faction.name)
        self.factions = [f for f in self.factions if f.faction_id != faction.faction_id]

    # ── Rival detection ───────────────────────────────────────────────────────

    def _check_rivals(self, engine: "SimEngine") -> None:
        for i, fa in enumerate(self.factions):
            for fb in self.factions[i + 1:]:
                if self._ideologies_oppose(fa.ideology, fb.ideology):
                    if fb.faction_id not in fa.rival_faction_ids:
                        fa.rival_faction_ids.append(fb.faction_id)
                        fb.rival_faction_ids.append(fa.faction_id)
                    self._rival_clash(fa, fb, engine)

    def _ideologies_oppose(
        self, ideo_a: dict[str, float], ideo_b: dict[str, float]
    ) -> bool:
        shared_keys = set(ideo_a) & set(ideo_b)
        if not shared_keys:
            return False
        opposing = sum(
            1 for k in shared_keys
            if abs(ideo_a[k] - ideo_b[k]) > 0.5
        )
        return opposing / max(len(shared_keys), 1) >= 0.5

    def _rival_clash(
        self, fa: Faction, fb: Faction, engine: "SimEngine"
    ) -> None:
        fa.influence = max(0.0, fa.influence - 0.05)
        fb.influence = max(0.0, fb.influence - 0.05)

        sim_map = {s.sim_id: s for s in engine.sims}
        for leader_id in (fa.leader_id, fb.leader_id):
            leader = sim_map.get(leader_id)
            if leader is not None and hasattr(leader, "moodlets"):
                leader.moodlets.add("tense", source="faction_rival_clash")

        self._record_event(
            engine.tick_count, fa.faction_id, "rival_clash",
            f"'{fa.name}' and '{fb.name}' clashed; both lost influence",
        )
        engine._bus.emit(
            "faction_rival_clash",
            faction_a=fa.faction_id,
            faction_b=fb.faction_id,
            tick=engine.tick_count,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _record_event(
        self, tick: int, faction_id: str, event_type: str, description: str
    ) -> None:
        self.events.append(
            FactionEvent(tick=tick, faction_id=faction_id,
                         event_type=event_type, description=description)
        )
        self.events = self.events[-50:]

    # ── Public API ────────────────────────────────────────────────────────────

    def get_sim_faction(self, sim_id: str) -> Faction | None:
        for faction in self.factions:
            if sim_id in faction.member_ids:
                return faction
        return None

    def summary(self) -> list[dict]:
        return [
            {
                "faction_id":        f.faction_id,
                "name":              f.name,
                "member_count":      len(f.member_ids),
                "leader_id":         f.leader_id,
                "influence":         round(f.influence, 3),
                "resources":         round(f.resources, 1),
                "goals":             list(f.goals),
                "collective_action": f.collective_action,
                "age_ticks":         f.age_ticks,
                "rival_count":       len(f.rival_faction_ids),
            }
            for f in self.factions
        ]

    def inject_context(self, sim_id: str, engine: "SimEngine") -> str:
        faction = self.get_sim_faction(sim_id)
        if faction is None:
            return ""
        goals_str = ", ".join(faction.goals)
        action_str = (
            f"; currently {faction.collective_action}"
            if faction.collective_action else ""
        )
        sim_map = {s.sim_id: s for s in engine.sims}
        sim = sim_map.get(sim_id)
        name = getattr(sim, "name", sim_id) if sim else sim_id
        return (
            f"{name} belongs to faction '{faction.name}' "
            f"pursuing {goals_str}{action_str}"
        )
