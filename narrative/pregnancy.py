"""
narrative/pregnancy.py — 3-tick gestation arc replacing instant child spawn.

Gestation stages:
  Stage 0 (tick of conception): pregnancy discovered
    → morning sickness moodlet, nesting wants, energy penalty
  Stage 1 (tick +1): growing
    → comfort/fun need shift, prenatal care interaction available
    → partner gets "support pregnant partner" goal
  Stage 2 (tick +2): birth
    → child spawns, birth event emitted, postpartum moodlet

Pregnancies are tracked on the engine as a dict of:
  {pregnancy_id: PregnancyRecord}

PregnancyRecord is advanced each tick by PregnancySystem.tick().
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine

MISCARRIAGE_CHANCE = 0.04     # per tick during gestation (low, neuroticism-modulated)


@dataclass
class PregnancyRecord:
    pregnancy_id: str
    parent_a_id: str
    parent_b_id: str
    stage: int = 0          # 0, 1, 2 → then birth fires
    ticks_in_stage: int = 0


class PregnancySystem:

    def begin(
        self, parent_a: "Sim", parent_b: "Sim", engine: "SimEngine"
    ) -> PregnancyRecord:
        """Start a pregnancy between two sims. Returns the record."""
        pid = uuid.uuid4().hex[:8]
        rec = PregnancyRecord(
            pregnancy_id=pid,
            parent_a_id=parent_a.sim_id,
            parent_b_id=parent_b.sim_id,
        )
        engine._pregnancies[pid] = rec
        self._apply_stage_0(parent_a, parent_b, engine)
        return rec

    def tick(self, engine: "SimEngine") -> None:
        finished = []
        for pid, rec in engine._pregnancies.items():
            pa = engine._sim_lookup.get(rec.parent_a_id)
            pb = engine._sim_lookup.get(rec.parent_b_id)
            if pa is None:
                finished.append(pid)
                continue

            rec.ticks_in_stage += 1
            if rec.ticks_in_stage < 1:
                continue  # wait one full tick per stage

            # Miscarriage check
            n = pa.ocean.get("neuroticism", 0.5)
            mc_chance = MISCARRIAGE_CHANCE * (1 + n * 0.5)
            import random
            if random.random() < mc_chance and rec.stage < 2:
                self._miscarriage(pa, pb, pid, engine)
                finished.append(pid)
                continue

            rec.stage += 1
            rec.ticks_in_stage = 0

            if rec.stage == 1:
                self._apply_stage_1(pa, pb, engine)
            elif rec.stage == 2:
                self._apply_stage_2(pa, pb, engine)
            elif rec.stage >= 3:
                # Birth
                self._birth(pa, pb, rec, engine)
                finished.append(pid)

        for pid in finished:
            engine._pregnancies.pop(pid, None)

    # ── Stage effects ─────────────────────────────────────────────────────────

    def _apply_stage_0(self, pa: "Sim", pb: "Sim | None", engine: "SimEngine") -> None:
        """Conception tick — discovery."""
        pa.emotion.add("surprise",   0.7, duration=5, source="pregnancy_discovered")
        pa.emotion.add("nervousness",0.5, duration=4, source="pregnancy_discovered")
        # Morning sickness moodlet
        if hasattr(pa, "moodlets"):
            pa.moodlets.add("uncomfortable", source="morning_sickness", override_duration=8)
        pa.needs.energy = max(0, pa.needs.energy - 10)

        if pb:
            pb.emotion.add("surprise", 0.7, duration=5, source="pregnancy_discovered")
            pb.emotion.add("joy",      0.5, duration=4, source="pregnancy_discovered")
            # Partner gets support goal
            try:
                from core.goals import set_goal_from_life_event
                set_goal_from_life_event(pb, "pregnancy_support", pa.sim_id,
                                         engine.tick_count, narrative="partner is pregnant")
            except Exception:
                pass

        engine._bus.emit("pregnancy_stage", stage=0, sim=pa.name, tick=engine.tick_count)

    def _apply_stage_1(self, pa: "Sim", pb: "Sim | None", engine: "SimEngine") -> None:
        """Mid-pregnancy — growing."""
        pa.needs.comfort = max(0, pa.needs.comfort - 5)
        pa.needs.fun     = max(0, pa.needs.fun     - 5)
        pa.emotion.add("anticipating", 0.6, duration=6, source="pregnancy_stage_1")
        engine._bus.emit("pregnancy_stage", stage=1, sim=pa.name, tick=engine.tick_count)

    def _apply_stage_2(self, pa: "Sim", pb: "Sim | None", engine: "SimEngine") -> None:
        """Late pregnancy — nesting."""
        pa.emotion.add("anticipating", 0.8, duration=4, source="pregnancy_stage_2")
        pa.emotion.add("nervousness",  0.5, duration=3, source="labour_approaching")
        engine._bus.emit("pregnancy_stage", stage=2, sim=pa.name, tick=engine.tick_count)

    def _birth(
        self, pa: "Sim", pb: "Sim | None", rec: "PregnancyRecord", engine: "SimEngine"
    ) -> None:
        """Call the engine's existing _spawn_child and apply birth effects."""
        if pb is None:
            pb = engine._sim_lookup.get(rec.parent_b_id)
        if pb is None:
            return

        child = engine._spawn_child(pa, pb)

        pa.emotion.add("joy",   1.0, duration=15, source="birth")
        pa.emotion.add("pride", 0.8, duration=10, source="birth")
        if hasattr(pa, "moodlets"):
            pa.moodlets.add("proud", source="new_baby")

        if pb:
            pb.emotion.add("joy",   1.0, duration=15, source="birth")
            pb.emotion.add("pride", 0.8, duration=10, source="birth")

        # Schedule a birth celebration social event
        if hasattr(engine, "social_events"):
            friends = sorted(
                [o for o in engine.sims if o.sim_id not in (pa.sim_id, pb.sim_id, child.sim_id)],
                key=lambda o: engine.relationships.get(pa.sim_id, o.sim_id).friendship,
                reverse=True,
            )[:6]
            engine.social_events.schedule_birthday(
                child, engine.tick_count, [f.sim_id for f in friends]
            )

    def _miscarriage(
        self, pa: "Sim", pb: "Sim | None", pid: str, engine: "SimEngine"
    ) -> None:
        pa.emotion.add("grief",      1.0, duration=20, source="miscarriage")
        pa.emotion.add("sadness",    0.8, duration=15, source="miscarriage")
        pa.grief_stage  = 0
        pa.grief_target = pb.sim_id if pb else ""
        if pb:
            pb.emotion.add("grief",  0.8, duration=15, source="miscarriage")
        engine._bus.emit("miscarriage", sim=pa.name, tick=engine.tick_count)
        import logging
        logging.getLogger(__name__).info("[Pregnancy] Miscarriage for %s", pa.name)
