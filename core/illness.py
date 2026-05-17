"""
core/illness.py — Illness and contagion system.

States: healthy → sick → recovering → healthy
Contagion: shared venue with low hygiene enables spread.
Severity: mild / moderate / severe (driven by neuroticism + hygiene at exposure).
Recovery: requires resting (high energy ticks above RECOVERY_ENERGY_MIN).

IllnessSystem.tick() is called from engine.run_tick().
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine

# Transmission
CONTAGION_BASE_CHANCE    = 0.08    # base per tick with a sick sim present
CONTAGION_HYGIENE_FACTOR = 0.015   # each hygiene point below 50 adds this much
CONTAGION_MIN_HYGIENE    = 50      # above this: no hygiene penalty to spread rate

# Illness duration by severity
ILLNESS_TICKS = {"mild": 4, "moderate": 8, "severe": 14}

# Recovery gate
RECOVERY_ENERGY_MIN = 55   # sim must maintain energy above this to recover

# Need decay multipliers while sick
SICK_NEED_DECAY = {
    "mild":     {"energy": 1.5, "fun": 1.3},
    "moderate": {"energy": 2.0, "fun": 1.5, "hygiene": 1.2},
    "severe":   {"energy": 2.5, "fun": 2.0, "hygiene": 1.5, "social": 1.3},
}


class IllnessSystem:

    def tick(self, engine: "SimEngine") -> None:
        # Collect currently sick sims
        sick = [s for s in engine.sims if getattr(s, "health_status", "healthy") == "sick"]

        # Spread to healthy sims sharing a venue (approximated: all active sims)
        if sick:
            self._spread(sick, engine)

        # Tick illness progress for all sick/recovering sims
        for sim in engine.sims:
            status = getattr(sim, "health_status", "healthy")
            if status == "sick":
                self._tick_sick(sim, engine)
            elif status == "recovering":
                self._tick_recovering(sim, engine)

    # ── Spread ────────────────────────────────────────────────────────────────

    def _spread(self, sick: list["Sim"], engine: "SimEngine") -> None:
        healthy = [
            s for s in engine.sims
            if getattr(s, "health_status", "healthy") == "healthy"
            and not getattr(s, "_sleeping", False)
        ]
        for carrier in sick:
            for target in healthy:
                hygiene = target.needs.hygiene
                hygiene_penalty = max(0, (CONTAGION_MIN_HYGIENE - hygiene)) * CONTAGION_HYGIENE_FACTOR
                chance = CONTAGION_BASE_CHANCE + hygiene_penalty
                if random.random() < chance:
                    self._infect(target, engine.tick_count)

    def _infect(self, sim: "Sim", tick: int) -> None:
        # Severity based on neuroticism and hygiene
        neuroticism = sim.ocean.get("neuroticism", 0.5)
        hygiene     = sim.needs.hygiene

        roll = random.random() + neuroticism * 0.2 - (hygiene / 200)
        if roll > 0.7:
            severity = "severe"
        elif roll > 0.4:
            severity = "moderate"
        else:
            severity = "mild"

        sim.health_status     = "sick"
        sim.illness_severity  = severity
        sim.illness_ticks_left = ILLNESS_TICKS[severity]

        sim.emotion.add("discomfort", 0.6, duration=4, source=f"sick:{severity}")
        import logging
        logging.getLogger(__name__).info(
            "[Illness] %s contracted %s illness", sim.name, severity
        )

    # ── Active illness ────────────────────────────────────────────────────────

    def _tick_sick(self, sim: "Sim", engine: "SimEngine") -> None:
        severity = getattr(sim, "illness_severity", "mild")
        decays   = SICK_NEED_DECAY.get(severity, {})

        for need_name, multiplier in decays.items():
            current = getattr(sim.needs, need_name, 50)
            setattr(sim.needs, need_name, max(0, current - (multiplier - 1.0) * 2.0))

        sim.illness_ticks_left = getattr(sim, "illness_ticks_left", 0) - 1

        if sim.illness_ticks_left <= 0:
            # Move to recovery phase
            sim.health_status = "recovering"
            sim.illness_ticks_left = ILLNESS_TICKS.get(severity, 4) // 2
            sim.emotion.add("relief", 0.4, duration=3, source="illness_clearing")
            engine._bus.emit("illness_update", sim=sim,
                             status="recovering", tick=engine.tick_count)

    def _tick_recovering(self, sim: "Sim", engine: "SimEngine") -> None:
        if sim.needs.energy < RECOVERY_ENERGY_MIN:
            return  # not resting enough — recovery stalls

        sim.illness_ticks_left = getattr(sim, "illness_ticks_left", 1) - 1
        if sim.illness_ticks_left <= 0:
            sim.health_status = "healthy"
            delattr(sim, "illness_severity") if hasattr(sim, "illness_severity") else None
            sim.emotion.add("relief", 0.6, duration=5, source="fully_recovered")
            engine._bus.emit("illness_update", sim=sim,
                             status="healthy", tick=engine.tick_count)
            import logging
            logging.getLogger(__name__).info("[Illness] %s fully recovered", sim.name)
