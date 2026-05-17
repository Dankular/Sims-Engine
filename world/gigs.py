"""
world/gigs.py — Freelance gig economy.

Sims with relevant skills can take on paid gigs that run for GIG_DURATION ticks.
During a gig, the sim earns simoleons and skill XP on completion, but the gig
fails (no pay) if their needs drop critically during work.

GigManager.tick() assigns new gigs and ticks active ones.
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine

GIG_DURATION      = 3      # ticks to complete
GIG_ASSIGN_CHANCE = 0.06   # per tick, per eligible sim without active gig
GIG_FAIL_NEED     = 15.0   # if any primary need drops below this, gig fails


@dataclass
class Gig:
    gig_id: str
    gig_type: str
    label: str
    required_skill: str
    min_skill_level: float
    pay: float             # simoleons on completion
    xp_reward: float       # skill XP on completion
    ticks_remaining: int
    failed: bool = False


_GIG_CATALOGUE = [
    # (gig_type, label, required_skill, min_level, base_pay, xp)
    ("freelance_coding",    "Freelance Coding Contract",   "logic",      3.0,  120, 1.5),
    ("portrait_commission", "Portrait Commission",         "creativity", 3.0,   90, 1.5),
    ("catering_gig",        "Private Catering Event",      "cooking",    3.0,   80, 1.2),
    ("photo_session",       "Photography Session",         "creativity", 2.0,   70, 1.0),
    ("tutoring_session",    "Private Tutoring",            "logic",      2.0,   60, 1.0),
    ("comedy_open_mic",     "Comedy Open Mic",             "comedy",     2.0,   50, 1.5),
    ("fitness_coaching",    "Personal Training Session",   "fitness",    2.0,   65, 1.0),
    ("recipe_consulting",   "Recipe Development Consult",  "cooking",    2.0,   55, 0.8),
    ("ghostwriting",        "Ghostwriting Contract",       "creativity", 4.0,  150, 2.0),
    ("app_development",     "App Development Contract",    "logic",      5.0,  200, 2.5),
]


class GigManager:

    def tick(self, engine: "SimEngine") -> None:
        tick = engine.tick_count
        for sim in engine.sims:
            if getattr(sim, "_sleeping", False):
                continue
            active: Gig | None = getattr(sim, "active_gig", None)

            if active is None:
                # Try to assign a new gig
                if random.random() < GIG_ASSIGN_CHANCE:
                    self._assign_gig(sim, tick)
            elif not isinstance(active, Gig):
                # Clear legacy dict-format gig entries from old code
                sim.active_gig = None
            else:
                self._tick_gig(sim, active, engine, tick)

    def _assign_gig(self, sim: "Sim", tick: int) -> None:
        eligible = [
            (gt, label, skill, min_lvl, pay, xp)
            for gt, label, skill, min_lvl, pay, xp in _GIG_CATALOGUE
            if sim.skills.levels.get(skill, 0) >= min_lvl
        ]
        if not eligible:
            return

        gt, label, skill, min_lvl, base_pay, xp = random.choice(eligible)
        level = sim.skills.levels.get(skill, min_lvl)
        # Pay scales with skill level
        pay = round(base_pay * (1 + (level - min_lvl) * 0.15), 2)

        sim.active_gig = Gig(
            gig_id=uuid.uuid4().hex[:8],
            gig_type=gt,
            label=label,
            required_skill=skill,
            min_skill_level=min_lvl,
            pay=pay,
            xp_reward=xp,
            ticks_remaining=GIG_DURATION,
        )
        sim.emotion.add("anticipating", 0.5, duration=3, source="new_gig")

    def _tick_gig(
        self, sim: "Sim", gig: "Gig", engine: "SimEngine", tick: int
    ) -> None:
        # Check failure conditions
        primary_needs = [sim.needs.energy, sim.needs.hunger, sim.needs.bladder]
        if any(n < GIG_FAIL_NEED for n in primary_needs):
            gig.failed = True
            sim.active_gig = None
            sim.emotion.add("disappointment", 0.6, duration=5, source="gig_failed")
            engine._bus.emit(
                "gig_completed",
                sim=sim,
                gig_type=gig.gig_type,
                label=gig.label,
                success=False,
                pay=0,
                tick=tick,
            )
            return

        gig.ticks_remaining -= 1
        if gig.ticks_remaining > 0:
            return  # still working

        # Gig completed
        sim.simoleons += gig.pay
        sim.skills.gain_xp(gig.required_skill, gig.xp_reward)
        sim.career_performance = min(100, sim.career_performance + 3)
        sim.active_gig = None
        sim.emotion.add("pride", 0.7, duration=6, source="gig_completed")
        sim.emotion.add("joy",   0.5, duration=4, source="gig_completed")

        # Boost creative reputation for creative gigs
        if gig.required_skill == "creativity":
            sim.creative_reputation = min(100, sim.creative_reputation + 3)

        # Celebrity score boost for performing gigs
        if gig.gig_type == "comedy_open_mic":
            sim.celebrity_score = min(100, sim.celebrity_score + 2)

        engine._bus.emit(
            "gig_completed",
            sim=sim,
            gig_type=gig.gig_type,
            label=gig.label,
            success=True,
            pay=gig.pay,
            tick=tick,
        )

        import logging
        logging.getLogger(__name__).info(
            "[Gig] %s completed '%s' — earned §%.0f", sim.name, gig.label, gig.pay
        )
