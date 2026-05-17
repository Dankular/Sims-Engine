from __future__ import annotations


class AncestryLedger:
    def __init__(self) -> None:
        self._parents_of: dict[str, list[str]] = {}
        self._children_of: dict[str, list[str]] = {}
        self._inheritance_notes: dict[str, list[dict]] = {}

    def register_birth(
        self, child_id: str, parent_ids: list[str], inherited: dict | None = None
    ) -> None:
        parents = list(parent_ids)
        self._parents_of[child_id] = parents
        for pid in parents:
            self._children_of.setdefault(pid, []).append(child_id)
        if inherited:
            self._inheritance_notes[child_id] = [dict(inherited)]

    def parents_of(self, sim_id: str) -> list[str]:
        return list(self._parents_of.get(sim_id, []))

    def children_of(self, sim_id: str) -> list[str]:
        return list(self._children_of.get(sim_id, []))

    def lineage_snapshot(self, sim_id: str) -> dict:
        return {
            "sim_id": sim_id,
            "parents": self.parents_of(sim_id),
            "children": self.children_of(sim_id),
            "inheritance_notes": list(self._inheritance_notes.get(sim_id, [])),
        }
