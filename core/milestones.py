from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MilestoneRecord:
    milestone_id: str
    tick: int
    source: str
    meta: dict


class MilestoneRegistry:
    def __init__(self) -> None:
        self._by_sim: dict[str, list[MilestoneRecord]] = {}
        self._achieved: set[tuple[str, str]] = set()

    def grant(
        self,
        sim_id: str,
        milestone_id: str,
        tick: int,
        source: str,
        meta: dict | None = None,
    ) -> bool:
        key = (sim_id, milestone_id)
        if key in self._achieved:
            return False
        self._achieved.add(key)
        self._by_sim.setdefault(sim_id, []).append(
            MilestoneRecord(
                milestone_id=milestone_id, tick=tick, source=source, meta=meta or {}
            )
        )
        return True

    def recent_for(self, sim_id: str, limit: int = 8) -> list[dict]:
        out = self._by_sim.get(sim_id, [])[-limit:]
        return [
            {
                "milestone_id": m.milestone_id,
                "tick": m.tick,
                "source": m.source,
                "meta": dict(m.meta),
            }
            for m in out
        ]
