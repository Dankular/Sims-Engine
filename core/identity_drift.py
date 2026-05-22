"""
core/identity_drift.py — OCEAN trait evolution from behavior + trauma.

Personality is not fixed. Repeated behaviors shift traits slowly; trauma
accelerates negative drift; recovery arcs (therapy, positive relationships,
time) enable partial reversion.

Drift rules:
  friendly interactions     → agreeableness +small
  intellectual interactions → openness +small
  toxic / mean interactions → agreeableness -, neuroticism +
  high-negative-valence events (trauma) → openness -, agreeableness -
  prolonged overwork (burnout) → conscientiousness -, neuroticism +
  romantic success          → extraversion +, neuroticism -
  grief unresolved          → neuroticism +, openness -
  recovery (therapy/rest)   → neuroticism -, openness + (slow)

Engine integration:
  engine.trait_drift = TraitDriftEngine()
  engine.trait_drift.record(sim, event_type, valence) ← in _apply_resolved()
  engine.trait_drift.tick(engine)                     ← in run_tick() every N ticks
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)

# How much a single event shifts a trait (base rate, scaled by valence magnitude)
BASE_DRIFT_RATE  = 0.003
TRAUMA_THRESHOLD = -0.6        # valence below this counts as trauma
TRAUMA_MULT      = 4.0         # trauma drifts traits 4× faster
RECOVERY_RATE    = 0.001       # passive reversion toward baseline per tick
MIN_TRAIT        = 0.05        # OCEAN floor (prevents absolute extremes)
MAX_TRAIT        = 0.95        # OCEAN ceiling

OCEAN_KEYS = ["openness", "conscientiousness", "extraversion",
              "agreeableness", "neuroticism"]


@dataclass
class DriftRecord:
    sim_id:     str
    event_type: str
    valence:    float
    tick:       int
    deltas:     dict  # trait → delta applied


class TraitDriftEngine:
    """
    Accumulates behavioral evidence and applies slow OCEAN trait drift.
    Drift is applied in batches every DRIFT_INTERVAL ticks to keep
    individual changes imperceptibly small but meaningful over long horizons.
    """

    DRIFT_INTERVAL = 10   # ticks between drift application
    MAX_HISTORY    = 200  # per-sim event buffer size

    def __init__(self) -> None:
        self._pending: dict[str, list[tuple[str, float]]] = {}  # sim_id → [(event, valence)]
        self._history: list[DriftRecord] = []
        self._baselines: dict[str, dict[str, float]] = {}  # sim_id → initial OCEAN

    # ── Event recording ───────────────────────────────────────────────────────

    def record(self, sim: "Sim", event_type: str, valence: float) -> None:
        """Buffer a behavioral event for batch drift processing."""
        self._pending.setdefault(sim.sim_id, []).append((event_type, valence))
        buf = self._pending[sim.sim_id]
        if len(buf) > self.MAX_HISTORY:
            self._pending[sim.sim_id] = buf[-self.MAX_HISTORY:]

        # Store baseline OCEAN on first encounter
        if sim.sim_id not in self._baselines:
            self._baselines[sim.sim_id] = dict(sim.ocean)

    # ── Drift application ─────────────────────────────────────────────────────

    def tick(self, engine: "SimEngine") -> None:
        if engine.tick_count % self.DRIFT_INTERVAL != 0:
            return
        for sim in engine.sims:
            events = self._pending.pop(sim.sim_id, [])
            if not events:
                # Passive recovery toward baseline
                self._apply_recovery(sim)
                continue
            deltas = self._compute_deltas(events)
            self._apply_deltas(sim, deltas, engine.tick_count)

    # ── Computation ───────────────────────────────────────────────────────────

    def _compute_deltas(self, events: list[tuple[str, float]]) -> dict[str, float]:
        deltas: dict[str, float] = {k: 0.0 for k in OCEAN_KEYS}

        for event_type, valence in events:
            rate = BASE_DRIFT_RATE
            if valence < TRAUMA_THRESHOLD:
                rate *= TRAUMA_MULT

            sign = math.copysign(1.0, valence)
            mag  = abs(valence)

            # Event-type rules
            if "friendly" in event_type or "repair" in event_type:
                deltas["agreeableness"] += sign * rate * mag
            if "intellectual" in event_type or "deep" in event_type:
                deltas["openness"] += sign * rate * mag * 0.5
            if "toxic" in event_type or "mean" in event_type:
                deltas["agreeableness"] -= rate * mag * 1.5
                deltas["neuroticism"]   += rate * mag
            if valence < TRAUMA_THRESHOLD:
                deltas["openness"]      -= rate * mag * 0.5
                deltas["agreeableness"] -= rate * mag * 0.5
                deltas["neuroticism"]   += rate * mag
            if "burnout" in event_type:
                deltas["conscientiousness"] -= rate * 2
                deltas["neuroticism"]       += rate * 1.5
            if "romantic" in event_type and valence > 0:
                deltas["extraversion"] += rate * mag * 0.5
                deltas["neuroticism"]  -= rate * mag * 0.3
            if "grief" in event_type:
                deltas["neuroticism"] += rate * mag * 0.8
                deltas["openness"]    -= rate * mag * 0.3
            if "achievement" in event_type and valence > 0:
                deltas["conscientiousness"] += rate * mag * 0.5
                deltas["extraversion"]      += rate * mag * 0.3

        return deltas

    def _apply_deltas(
        self, sim: "Sim", deltas: dict[str, float], tick: int
    ) -> None:
        applied: dict[str, float] = {}
        for key, delta in deltas.items():
            if abs(delta) < 1e-6:
                continue
            old = sim.ocean.get(key, 0.5)
            new = max(MIN_TRAIT, min(MAX_TRAIT, old + delta))
            if abs(new - old) > 1e-6:
                sim.ocean[key] = round(new, 4)
                applied[key] = round(delta, 5)

        if applied:
            self._history.append(DriftRecord(
                sim_id=sim.sim_id, event_type="batch",
                valence=0.0, tick=tick, deltas=applied,
            ))
            self._history = self._history[-1000:]
            logger.debug(
                "[TraitDrift] %s: %s",
                sim.name,
                " ".join(f"{k}{'+' if v > 0 else ''}{v:.4f}" for k, v in applied.items()),
            )

    def _apply_recovery(self, sim: "Sim") -> None:
        """Passive slow reversion toward initial baseline."""
        baseline = self._baselines.get(sim.sim_id)
        if not baseline:
            return
        for key in OCEAN_KEYS:
            current = sim.ocean.get(key, 0.5)
            base    = baseline.get(key, 0.5)
            gap     = base - current
            if abs(gap) < 0.001:
                continue
            # Drift 10% of the gap toward baseline each interval
            new = current + gap * RECOVERY_RATE * 10
            sim.ocean[key] = round(max(MIN_TRAIT, min(MAX_TRAIT, new)), 4)

    # ── Stats ─────────────────────────────────────────────────────────────────

    def drift_magnitude(self, sim: "Sim") -> float:
        """Euclidean distance of current OCEAN from baseline."""
        baseline = self._baselines.get(sim.sim_id, {})
        if not baseline:
            return 0.0
        return math.sqrt(sum(
            (sim.ocean.get(k, 0.5) - baseline.get(k, 0.5)) ** 2
            for k in OCEAN_KEYS
        ))

    def summary(self, sim: "Sim") -> dict:
        baseline = self._baselines.get(sim.sim_id, {})
        return {
            "current":   {k: round(sim.ocean.get(k, 0.5), 3) for k in OCEAN_KEYS},
            "baseline":  {k: round(baseline.get(k, 0.5), 3) for k in OCEAN_KEYS},
            "drift_mag": round(self.drift_magnitude(sim), 4),
        }
