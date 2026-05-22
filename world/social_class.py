"""
world/social_class.py — Wealth-tier classification and class-mobility tracking.

Classifies each sim into a wealth tier every CLASS_CHECK_INTERVAL ticks,
detects upward/downward mobility, applies class-specific effects, and exposes
a class_affinity_score for the scheduler.

SocialClassSystem.tick(engine) is called every tick from SimEngine.run_tick().
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.engine import SimEngine
    from core.sim import Sim

logger = logging.getLogger(__name__)

__all__ = ["SocialClassSystem"]

# (tier_name, min_simoleons_inclusive, max_simoleons_exclusive)
WEALTH_TIERS: list[tuple[str, float, float]] = [
    ("destitute", 0,       100),
    ("poor",      100,     500),
    ("working",   500,     2000),
    ("middle",    2000,    10000),
    ("wealthy",   10000,   50000),
    ("elite",     50000,   float("inf")),
]

_TIER_NAMES: list[str] = [t[0] for t in WEALTH_TIERS]
_TIER_INDEX: dict[str, int] = {name: i for i, name in enumerate(_TIER_NAMES)}


def _classify(sim: "Sim") -> str:
    wealth = max(0.0, sim.simoleons)
    for name, lo, hi in WEALTH_TIERS:
        if lo <= wealth < hi:
            return name
    return "elite"


class SocialClassSystem:
    CLASS_CHECK_INTERVAL: int = 5

    def __init__(self) -> None:
        self._sim_classes: dict[str, str] = {}
        self._class_history: list[dict] = []
        self._upward_moves: int = 0
        self._downward_moves: int = 0

    # ── Public tick ───────────────────────────────────────────────────────────

    def tick(self, engine: "SimEngine") -> None:
        try:
            self._apply_class_effects(engine)
            if engine.tick_count % self.CLASS_CHECK_INTERVAL == 0:
                self._classify_all(engine)
                self._snapshot(engine)
        except Exception as exc:
            logger.debug("[SocialClass] tick error: %s", exc)

    # ── Classification ────────────────────────────────────────────────────────

    def _classify_all(self, engine: "SimEngine") -> None:
        sim_map = {s.sim_id: s for s in engine.sims}
        for sim in engine.sims:
            new_tier = _classify(sim)
            old_tier = self._sim_classes.get(sim.sim_id)
            if old_tier is not None and old_tier != new_tier:
                self._on_class_change(sim, old_tier, new_tier, engine)
            self._sim_classes[sim.sim_id] = new_tier

    def _on_class_change(
        self,
        sim: "Sim",
        old_tier: str,
        new_tier: str,
        engine: "SimEngine",
    ) -> None:
        old_idx = _TIER_INDEX.get(old_tier, 0)
        new_idx = _TIER_INDEX.get(new_tier, 0)
        if new_idx > old_idx:
            self._upward_moves += 1
            if hasattr(sim, "moodlets"):
                sim.moodlets.add("proud", source="class_rise")
        else:
            self._downward_moves += 1
            if hasattr(sim, "moodlets"):
                sim.moodlets.add("publicly_humiliated", source="class_fall")

        engine._bus.emit(
            "class_change",
            sim_id=sim.sim_id,
            name=sim.name,
            old_tier=old_tier,
            new_tier=new_tier,
            tick=engine.tick_count,
        )
        logger.debug(
            "[SocialClass] %s: %s → %s", sim.name, old_tier, new_tier
        )

    # ── Per-tick effects ──────────────────────────────────────────────────────

    def _apply_class_effects(self, engine: "SimEngine") -> None:
        for sim in engine.sims:
            tier = self._sim_classes.get(sim.sim_id)
            if tier is None:
                continue
            if tier == "elite":
                sim.reputation_score = min(100.0, sim.reputation_score + 0.3)
                sim.celebrity_score = min(100.0, sim.celebrity_score + 0.1)
            elif tier == "destitute":
                if sim.needs.hunger < 30 and hasattr(sim, "moodlets"):
                    sim.moodlets.add("stressed", source="destitute_hunger")

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def _snapshot(self, engine: "SimEngine") -> None:
        counts: dict[str, int] = {t[0]: 0 for t in WEALTH_TIERS}
        for tier in self._sim_classes.values():
            counts[tier] = counts.get(tier, 0) + 1
        self._class_history.append(
            {"tick": engine.tick_count, "counts": dict(counts)}
        )
        self._class_history = self._class_history[-50:]

    # ── Public API ────────────────────────────────────────────────────────────

    def classify(self, sim: "Sim") -> str:
        return _classify(sim)

    def get_class(self, sim_id: str) -> str:
        return self._sim_classes.get(sim_id, "unknown")

    def class_affinity_score(self, sim_a_id: str, sim_b_id: str) -> float:
        tier_a = self._sim_classes.get(sim_a_id)
        tier_b = self._sim_classes.get(sim_b_id)
        if tier_a is None or tier_b is None:
            return 0.0
        idx_a = _TIER_INDEX.get(tier_a, 0)
        idx_b = _TIER_INDEX.get(tier_b, 0)
        diff = abs(idx_a - idx_b)
        if diff == 0:
            return 0.15
        if diff >= 2:
            return -0.1
        return 0.0

    def mobility_summary(self) -> dict:
        if not self._class_history:
            tier_counts: dict[str, int] = {}
            gini_approx = 0.0
        else:
            tier_counts = dict(self._class_history[-1]["counts"])
            total = max(1, sum(tier_counts.values()))
            # Weighted Gini approximation using tier midpoints
            midpoints = [50, 300, 1250, 6000, 30000, 75000]
            weights: list[float] = []
            for t in _TIER_NAMES:
                count = tier_counts.get(t, 0)
                weights.extend([midpoints[_TIER_INDEX[t]]] * count)
            weights.sort()
            n = len(weights)
            if n > 0 and sum(weights) > 0:
                cum = 0.0
                lorenz = 0.0
                total_w = sum(weights)
                for w in weights:
                    cum += w
                    lorenz += cum / total_w
                gini_approx = round(
                    max(0.0, min(1.0, 1.0 - (2.0 * lorenz / n) + (1.0 / n))),
                    4,
                )
            else:
                gini_approx = 0.0
        return {
            "tier_counts":      tier_counts,
            "gini_approx":      gini_approx,
            "upward_moves":     self._upward_moves,
            "downward_moves":   self._downward_moves,
        }

    def summary(self) -> dict:
        return self.mobility_summary()
