from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)

__all__ = ["CohortSnapshot", "DemographicEngine"]


def _gini_from_sorted(values: list[float]) -> float:
    """Trapezoidal Gini from a pre-sorted list of non-negative values."""
    n = len(values)
    if n == 0:
        return 0.0
    total = sum(values)
    if total == 0.0:
        return 0.0
    cumulative = 0.0
    lorenz_area = 0.0
    for w in values:
        cumulative += w
        lorenz_area += cumulative / total
    gini_val = 1.0 - (2.0 * lorenz_area / n) + (1.0 / n)
    return round(max(0.0, min(1.0, gini_val)), 4)


def _life_stage_from_age(age: int) -> str:
    if age <= 12:
        return "child"
    if age <= 17:
        return "teen"
    if age <= 25:
        return "young_adult"
    if age <= 59:
        return "adult"
    return "elder"


@dataclass
class CohortSnapshot:
    tick: int
    counts: dict[str, int]
    median_simoleons: float
    gini: float
    avg_happiness: float


class DemographicEngine:
    _STAGES = ("child", "teen", "young_adult", "adult", "elder")

    def __init__(self) -> None:
        self.snapshots: list[CohortSnapshot] = []
        self.birth_pressure: float = 0.5
        self.emigration_threshold: float = 0.3

    def tick(self, engine: "SimEngine") -> None:
        if engine.tick_count % 5 != 0:
            return

        sims = engine.sims
        if not sims:
            return

        counts: dict[str, int] = {s: 0 for s in self._STAGES}
        for sim in sims:
            age = int(sim.profile.get("age", 25))
            counts[_life_stage_from_age(age)] += 1

        wealth = sorted(s.simoleons for s in sims)
        n = len(wealth)
        mid = n // 2
        median_simoleons = (
            float(wealth[mid])
            if n % 2 == 1
            else (float(wealth[mid - 1]) + float(wealth[mid])) / 2.0
        )

        avg_happiness = sum(
            getattr(s.emotion, "dominant_valence", 0.0) for s in sims
        ) / n

        gini = _gini_from_sorted(wealth)

        snap = CohortSnapshot(
            tick=engine.tick_count,
            counts=dict(counts),
            median_simoleons=round(median_simoleons, 2),
            gini=gini,
            avg_happiness=round(avg_happiness, 4),
        )
        self.snapshots.append(snap)
        if len(self.snapshots) > 100:
            self.snapshots = self.snapshots[-100:]

        if avg_happiness > 0.5 and median_simoleons > 300:
            self.birth_pressure = min(1.0, self.birth_pressure + 0.02)
        else:
            self.birth_pressure = max(0.1, self.birth_pressure - 0.01)

        for stage in self._STAGES:
            if counts[stage] == 0:
                engine._bus.emit(
                    "demographic_gap",
                    stage=stage,
                    tick=engine.tick_count,
                )
                logger.info("[Demographics] demographic_gap: no sims in stage '%s'", stage)

        if gini > 0.6:
            engine._bus.emit(
                "wealth_inequality_crisis",
                gini=gini,
                tick=engine.tick_count,
            )
            logger.warning("[Demographics] wealth_inequality_crisis gini=%.4f", gini)
            self._apply_inequality_stress(sims, wealth, engine)

    def _apply_inequality_stress(
        self,
        sims: list,
        wealth: list[float],
        engine: "SimEngine",
    ) -> None:
        n = len(sims)
        if n == 0:
            return
        cutoff_idx = max(0, n // 4 - 1)
        cutoff_wealth = wealth[cutoff_idx] if cutoff_idx < n else 0.0

        for sim in sims:
            if sim.simoleons <= cutoff_wealth and hasattr(sim, "moodlets"):
                sim.moodlets.add("stressed", source="wealth_inequality")

    def summary(self) -> dict:
        latest = self.snapshots[-1] if self.snapshots else None
        return {
            "cohort_counts": dict(latest.counts) if latest else {s: 0 for s in self._STAGES},
            "gini": latest.gini if latest else 0.0,
            "birth_pressure": round(self.birth_pressure, 4),
            "snapshot_count": len(self.snapshots),
            "median_simoleons": latest.median_simoleons if latest else 0.0,
            "avg_happiness": latest.avg_happiness if latest else 0.0,
        }

    def cohort_trend(self, life_stage: str, last_n: int = 10) -> list[int]:
        snaps = self.snapshots[-last_n:] if last_n > 0 else self.snapshots
        return [s.counts.get(life_stage, 0) for s in snaps]

    def inject_birth_pressure(self, engine: "SimEngine") -> float:
        return self.birth_pressure
