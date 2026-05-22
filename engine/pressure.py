"""
engine/pressure.py — Pressure-driven endogenous event synthesis.

Rather than scheduled templates, events emerge from accumulated simulation
tension. A PressureIndex computes four orthogonal pressure dimensions per sim:

  financial_pressure   — simoleons / debt / expenses
  romance_pressure     — loneliness / unfulfilled romance / rejection history
  institutional_pressure — reputation / career risk / sanction count
  health_pressure      — energy / illness / age / moodlet load

When any index crosses a threshold, a novel event is synthesised from the
tension vector — no templates. The event type, target, and narrative are
derived from which pressures are highest and how they combine.

Engine integration:
  self.pressure_engine = PressureIndex()
  self.pressure_engine.tick(engine)  ← called in run_tick()
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
PRESSURE_EVENT_THRESHOLD = 0.72   # above this → spawn event
PRESSURE_CRITICAL        = 0.90   # above this → hard consequence eligible
MIN_TICKS_BETWEEN_EVENTS = 8      # per-sim cooldown


@dataclass
class PressureVector:
    financial:     float = 0.0
    romance:       float = 0.0
    institutional: float = 0.0
    health:        float = 0.0

    def max_dim(self) -> tuple[str, float]:
        dims = {
            "financial":     self.financial,
            "romance":       self.romance,
            "institutional": self.institutional,
            "health":        self.health,
        }
        key = max(dims, key=dims.get)  # type: ignore[arg-type]
        return key, dims[key]

    def total(self) -> float:
        return (self.financial + self.romance + self.institutional + self.health) / 4


class PressureIndex:
    """
    Computes per-sim pressure vectors and synthesises novel events when
    pressure exceeds threshold.  Events are contextual — derived from the
    tension profile, not drawn from a fixed template set.
    """

    def __init__(self) -> None:
        self._vectors:   dict[str, PressureVector] = {}
        self._last_event: dict[str, int] = {}           # sim_id → tick
        self._event_log: list[dict] = []

    # ── Pressure computation ──────────────────────────────────────────────────

    def compute(self, sim: "Sim", engine: "SimEngine") -> PressureVector:
        needs = sim.needs

        # Financial: low simoleons, high debt, recent expensive obligations
        fin  = max(0.0, 1.0 - sim.simoleons / 1000.0)
        fin  = min(1.0, fin + (0.3 if sim.simoleons < 0 else 0))

        # Romance: low romance scores, loneliness, unfulfilled want
        rom_wants = any(
            w.label in ("find_romance", "first_kiss", "get_married")
            for w in getattr(sim, "active_wants", [])
        )
        rom  = max(0.0, 1.0 - needs.social / 100.0) * 0.5
        rom += 0.3 if rom_wants else 0.0
        rom += 0.2 if getattr(sim, "grief_stage", 0) >= 2 else 0.0
        rom  = min(1.0, rom)

        # Institutional: reputation floor, career at risk
        inst  = max(0.0, -sim.reputation_score / 100.0)   # neg rep → high pressure
        inst += max(0.0, 1.0 - sim.career_performance / 100.0) * 0.3
        # Pending hard consequences add pressure
        hc_count = len(engine.hard_consequences.active_for(sim)) if hasattr(engine, "hard_consequences") else 0
        inst += hc_count * 0.15
        inst  = min(1.0, inst)

        # Health: low energy, illness, age
        hlt  = max(0.0, 1.0 - needs.energy / 100.0) * 0.4
        hlt += max(0.0, 1.0 - needs.hunger / 100.0) * 0.3
        if getattr(sim, "is_ill", False):
            hlt += 0.3
        age = sim.profile.get("age", 25)
        if age >= 70:
            hlt += 0.2
        hlt  = min(1.0, hlt)

        pv = PressureVector(
            financial=round(fin, 3),
            romance=round(rom, 3),
            institutional=round(inst, 3),
            health=round(hlt, 3),
        )
        self._vectors[sim.sim_id] = pv
        return pv

    # ── Event synthesis ───────────────────────────────────────────────────────

    def _synthesise_event(
        self, sim: "Sim", pv: PressureVector, engine: "SimEngine"
    ) -> dict | None:
        """Derive an event from the pressure vector without a template."""
        dim, magnitude = pv.max_dim()

        # Pick a second sim as catalyst (if applicable)
        others = [s for s in engine.sims if s.sim_id != sim.sim_id]
        catalyst = random.choice(others) if others else None

        if dim == "financial":
            if magnitude > PRESSURE_CRITICAL:
                return {
                    "event_type":  "financial_crisis",
                    "sim_id":      sim.sim_id,
                    "narrative":   (
                        f"{sim.name} faces a financial crisis — "
                        f"simoleons at {sim.simoleons:.0f}. "
                        f"Desperate measures may follow."
                    ),
                    "intensity":   magnitude,
                    "visibility":  "household",
                }
            return {
                "event_type":  "financial_stress",
                "sim_id":      sim.sim_id,
                "narrative":   f"{sim.name} is under financial strain.",
                "intensity":   magnitude,
                "visibility":  "private",
            }

        elif dim == "romance":
            if catalyst:
                return {
                    "event_type":  "romantic_tension",
                    "sim_id":      sim.sim_id,
                    "target_id":   catalyst.sim_id,
                    "narrative":   (
                        f"{sim.name} fixates on {catalyst.name}, "
                        f"driven by loneliness and unresolved desire."
                    ),
                    "intensity":   magnitude,
                    "visibility":  "private",
                }

        elif dim == "institutional":
            return {
                "event_type":  "reputation_crisis",
                "sim_id":      sim.sim_id,
                "narrative":   (
                    f"{sim.name}'s reputation ({sim.reputation_score:.0f}) "
                    f"is collapsing — institutional consequences imminent."
                ),
                "intensity":   magnitude,
                "visibility":  "public" if magnitude > PRESSURE_CRITICAL else "witnessed",
            }

        elif dim == "health":
            return {
                "event_type":  "health_breakdown",
                "sim_id":      sim.sim_id,
                "narrative":   (
                    f"{sim.name} is showing signs of exhaustion. "
                    f"Energy={sim.needs.energy:.0f}, hunger={sim.needs.hunger:.0f}."
                ),
                "intensity":   magnitude,
                "visibility":  "household",
            }

        return None

    # ── Tick ──────────────────────────────────────────────────────────────────

    def tick(self, engine: "SimEngine") -> None:
        for sim in engine.sims:
            pv = self.compute(sim, engine)
            total = pv.total()
            if total < PRESSURE_EVENT_THRESHOLD:
                continue

            last = self._last_event.get(sim.sim_id, -MIN_TICKS_BETWEEN_EVENTS)
            if engine.tick_count - last < MIN_TICKS_BETWEEN_EVENTS:
                continue

            event = self._synthesise_event(sim, pv, engine)
            if not event:
                continue

            self._last_event[sim.sim_id] = engine.tick_count
            self._event_log.append({**event, "tick": engine.tick_count})
            self._event_log = self._event_log[-200:]

            engine._bus.emit("pressure_event", **event, tick=engine.tick_count)

            # Critical pressure → check hard consequences
            if total > PRESSURE_CRITICAL and hasattr(engine, "hard_consequences"):
                engine.hard_consequences.check_auto_triggers(
                    sim, engine.tick_count, bus=engine._bus
                )

            logger.debug(
                "[Pressure] %s → %s (%.2f total)",
                sim.name, event["event_type"], total,
            )

    # ── Stats ─────────────────────────────────────────────────────────────────

    def vector_for(self, sim_id: str) -> dict:
        pv = self._vectors.get(sim_id, PressureVector())
        return {
            "financial":     pv.financial,
            "romance":       pv.romance,
            "institutional": pv.institutional,
            "health":        pv.health,
            "total":         round(pv.total(), 3),
        }

    def recent_events(self, n: int = 20) -> list[dict]:
        return self._event_log[-n:]

    def most_pressured(self, top_n: int = 5) -> list[dict]:
        ranked = sorted(
            self._vectors.items(),
            key=lambda kv: -kv[1].total(),
        )[:top_n]
        return [{"sim_id": sid, **self.vector_for(sid)} for sid, _ in ranked]
