"""
world/history.py — World chronicle that remembers civilization-scale events.

WorldChronicle records major events (deaths, faction wars, economic crashes,
milestones), decays their district impact over time, and provides compact
narrative strings for LLM context injection.

WorldChronicle.tick(engine) is called every tick from SimEngine.run_tick().
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)

__all__ = ["HistoricalEvent", "WorldChronicle"]


@dataclass
class HistoricalEvent:
    tick: int
    event_type: str
    location: str
    description: str
    participants: list[str] = field(default_factory=list)
    impact: float = 0.0
    tags: list[str] = field(default_factory=list)


class WorldChronicle:
    MAX_EVENTS = 500
    IMPACT_DECAY_RATE = 0.002

    def __init__(self) -> None:
        self.events: list[HistoricalEvent] = []
        self._district_accumulated_impact: dict[str, float] = {}
        self._recorded_sims: set[str] = set()
        self._last_economy_phase: str = ""

    # ── Record ────────────────────────────────────────────────────────────────

    def record(
        self,
        tick: int,
        event_type: str,
        description: str,
        participants: list[str] | None = None,
        location: str = "global",
        impact: float = 0.0,
        tags: list[str] | None = None,
    ) -> None:
        evt = HistoricalEvent(
            tick=tick,
            event_type=event_type,
            location=location,
            description=description,
            participants=list(participants or []),
            impact=impact,
            tags=list(tags or []),
        )
        self.events.append(evt)
        if len(self.events) > self.MAX_EVENTS:
            self.events = self.events[-self.MAX_EVENTS:]

        self._district_accumulated_impact[location] = (
            self._district_accumulated_impact.get(location, 0.0) + impact
        )

    # ── Tick ──────────────────────────────────────────────────────────────────

    def tick(self, engine: "SimEngine") -> None:
        self._decay_impacts()

        if engine.tick_count % 10 == 0:
            self._apply_impacts_to_neighborhoods(engine)

        if engine.tick_count % 20 == 0:
            self._scan_emergent_events(engine)

    def _decay_impacts(self) -> None:
        for loc in list(self._district_accumulated_impact):
            val = self._district_accumulated_impact[loc]
            if val > 0:
                self._district_accumulated_impact[loc] = max(
                    0.0, val - self.IMPACT_DECAY_RATE
                )
            elif val < 0:
                self._district_accumulated_impact[loc] = min(
                    0.0, val + self.IMPACT_DECAY_RATE
                )

    def _apply_impacts_to_neighborhoods(self, engine: "SimEngine") -> None:
        neighborhoods_sys = getattr(engine, "neighborhoods", None)
        if neighborhoods_sys is None:
            return
        world = getattr(neighborhoods_sys, "world", None)
        if world is None:
            return

        for district in world.districts:
            impact = self._district_accumulated_impact.get(district.id, 0.0)
            if impact == 0.0:
                continue
            for neighborhood in district.neighborhoods:
                di = neighborhood.district_identity
                if impact > 0:
                    di["wealth"] = min(1.0, di.get("wealth", 0.5) + 0.01)
                    di["crime"] = max(0.0, di.get("crime", 0.2) - 0.005)
                else:
                    di["crime"] = min(1.0, di.get("crime", 0.2) + 0.01)
                    di["wealth"] = max(0.0, di.get("wealth", 0.5) - 0.005)

    def _scan_emergent_events(self, engine: "SimEngine") -> None:
        tick = engine.tick_count

        for sim in engine.sims:
            key_wealth = f"wealth_milestone_{sim.sim_id}"
            if (
                sim.simoleons > 10000
                and key_wealth not in self._recorded_sims
            ):
                self._recorded_sims.add(key_wealth)
                self.record(
                    tick=tick,
                    event_type="wealth_milestone",
                    description=(
                        f"{sim.name} accumulated over §10,000 simoleons, "
                        f"becoming one of the wealthiest sims in the world."
                    ),
                    participants=[sim.sim_id],
                    location="global",
                    impact=0.2,
                    tags=["economic", "prosperity"],
                )
                logger.debug("[Chronicle] wealth_milestone: %s", sim.name)

            key_celeb = f"celebrity_emerged_{sim.sim_id}"
            if (
                sim.reputation_score > 80
                and key_celeb not in self._recorded_sims
            ):
                self._recorded_sims.add(key_celeb)
                self.record(
                    tick=tick,
                    event_type="celebrity_emerged",
                    description=(
                        f"{sim.name} rose to celebrity status with a "
                        f"reputation score of {sim.reputation_score:.0f}."
                    ),
                    participants=[sim.sim_id],
                    location="global",
                    impact=0.15,
                    tags=["social", "fame"],
                )
                logger.debug("[Chronicle] celebrity_emerged: %s", sim.name)

        macro = getattr(engine, "macro_economy", None)
        if macro is not None:
            current_phase = macro.cycle.phase
            if current_phase != self._last_economy_phase and self._last_economy_phase:
                self.record(
                    tick=tick,
                    event_type="economic_phase",
                    description=(
                        f"The economy shifted from {self._last_economy_phase} "
                        f"to {current_phase}."
                    ),
                    location="global",
                    impact=-0.3 if current_phase in ("trough", "contraction") else 0.3,
                    tags=["economic", "political"],
                )
                logger.debug(
                    "[Chronicle] economic_phase: %s → %s",
                    self._last_economy_phase, current_phase,
                )
            self._last_economy_phase = current_phase

        factions = getattr(engine, "factions", None)
        if factions is not None:
            for faction in factions.factions:
                if faction.age_ticks == 1:
                    self.record(
                        tick=tick,
                        event_type="faction_formed",
                        description=(
                            f"Faction '{faction.name}' emerged, uniting "
                            f"{len(faction.member_ids)} sims around shared beliefs."
                        ),
                        participants=list(faction.member_ids),
                        location="global",
                        impact=0.05,
                        tags=["political", "social"],
                    )
                    logger.debug("[Chronicle] faction_formed: %s", faction.name)

    # ── Narrative output ──────────────────────────────────────────────────────

    def digest(
        self,
        last_n: int = 5,
        location: str | None = None,
        event_types: list[str] | None = None,
    ) -> str:
        filtered = self.events
        if location is not None:
            filtered = [e for e in filtered if e.location == location]
        if event_types is not None:
            filtered = [e for e in filtered if e.event_type in event_types]
        recent = filtered[-last_n:]
        parts = [
            f"[tick {e.tick}] {e.description}"
            for e in reversed(recent)
        ]
        return " ".join(parts)

    def district_narrative(self, district_id: str) -> str:
        district_events = [
            e for e in self.events if e.location == district_id
        ][-5:]
        if not district_events:
            return f"No recorded history for district '{district_id}'."

        accumulated = self._district_accumulated_impact.get(district_id, 0.0)
        mood = "prosperous" if accumulated > 0.2 else (
            "troubled" if accumulated < -0.2 else "unremarkable"
        )
        sentences: list[str] = [
            f"This district has been {mood} in recent memory."
        ]
        for evt in district_events[-2:]:
            sentences.append(
                f"{evt.description.rstrip('.')} at tick {evt.tick}."
            )
        return " ".join(sentences)

    def sim_narrative(self, sim_id: str, last_n: int = 3) -> str:
        sim_events = [
            e for e in self.events if sim_id in e.participants
        ][-last_n:]
        if not sim_events:
            return ""
        parts = [
            f"[tick {e.tick}] {e.description}"
            for e in reversed(sim_events)
        ]
        return " ".join(parts)

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        by_type: dict[str, int] = {}
        for evt in self.events:
            by_type[evt.event_type] = by_type.get(evt.event_type, 0) + 1

        most_impacted = (
            max(
                self._district_accumulated_impact,
                key=lambda k: abs(self._district_accumulated_impact[k]),
            )
            if self._district_accumulated_impact else "none"
        )
        return {
            "total_events": len(self.events),
            "by_type": by_type,
            "most_impacted_district": most_impacted,
        }
