"""
engine/budget.py — Budgeted tick scheduler.

Maintains three queues (active, background, dormant) and exposes
round-robin batches so tick cost is bounded to O(ACTIVE_BUDGET + BG_BUDGET)
regardless of total sim count.

Active sims rotate through the queue so every sim eventually gets full
processing — latency is bounded, not priority-inverted.
"""
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim


class BudgetedScheduler:
    """
    Partitions sims into three processing tiers each tick.

    Active batch  (next_active_batch):  full sim.tick() + arcs + goals
    Background batch (next_bg_batch):   heuristic interaction only
    Dormant sims (dormant_sims):        minimal need decay, no tick

    Sims are classified by their current LOD tier (set by assign_lod_tiers
    before rebuild() is called).  The round-robin rotation ensures no sim
    is permanently starved even at high sim counts.
    """

    def __init__(self, budget: int = 8, bg_budget: int = 4) -> None:
        self.budget = budget
        self.bg_budget = bg_budget
        self._active: deque["Sim"] = deque()
        self._background: deque["Sim"] = deque()
        self._dormant: list["Sim"] = []

    def rebuild(self, sims: list["Sim"]) -> None:
        """Re-classify sims by LOD tier. Call once per tick before batching."""
        from sim_types.enums import LODTier

        active_ids = {s.sim_id for s in self._active}
        bg_ids = {s.sim_id for s in self._background}

        new_active: deque["Sim"] = deque()
        new_bg: deque["Sim"] = deque()
        new_dormant: list["Sim"] = []

        for sim in sims:
            if sim.lod_tier == LODTier.ACTIVE:
                new_active.append(sim)
            elif sim.lod_tier == LODTier.BACKGROUND:
                new_bg.append(sim)
            else:
                new_dormant.append(sim)

        # Preserve rotation order for sims already in the queues
        def _reorder(new_q: deque["Sim"], old_ids: set[str]) -> deque["Sim"]:
            known = [s for s in new_q if s.sim_id in old_ids]
            fresh = [s for s in new_q if s.sim_id not in old_ids]
            return deque(known + fresh)

        self._active = _reorder(new_active, active_ids)
        self._background = _reorder(new_bg, bg_ids)
        self._dormant = new_dormant

    def next_active_batch(self) -> list["Sim"]:
        """Return up to `budget` sims from the active queue (round-robin)."""
        n = min(self.budget, len(self._active))
        batch: list["Sim"] = []
        for _ in range(n):
            sim = self._active.popleft()
            batch.append(sim)
            self._active.append(sim)
        return batch

    def next_bg_batch(self) -> list["Sim"]:
        """Return up to `bg_budget` sims from the background queue."""
        n = min(self.bg_budget, len(self._background))
        batch: list["Sim"] = []
        for _ in range(n):
            sim = self._background.popleft()
            batch.append(sim)
            self._background.append(sim)
        return batch

    def dormant_sims(self) -> list["Sim"]:
        return self._dormant

    def all_active(self) -> list["Sim"]:
        """All sims in the active queue (used for pair selection)."""
        return list(self._active)

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def background_count(self) -> int:
        return len(self._background)

    @property
    def dormant_count(self) -> int:
        return len(self._dormant)

    def stats(self) -> dict[str, int]:
        return {
            "active": self.active_count,
            "background": self.background_count,
            "dormant": self.dormant_count,
        }
