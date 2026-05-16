from dataclasses import dataclass, field

from config import (
    MAX_MEMORIES,
    REL_ACQUAINTANCE,
    REL_BEST,
    REL_CLOSE,
    REL_FRIEND,
)


@dataclass
class RelationshipRecord:
    friendship: float = 0.0
    romance: float = 0.0
    interactions: int = 0
    memories: list[dict] = field(default_factory=list)
    # Toxic cycle tracking (Gap 4)
    in_toxic_cycle: bool = False
    toxic_cycle_phase: str = "none"   # "love_bombing" | "devaluation" | "repair" | "none"
    toxic_cycle_tick: int = 0         # tick when current phase started

    def state_label(self) -> str:
        if self.friendship >= REL_BEST:
            return "best friends"
        if self.friendship >= REL_CLOSE:
            return "close friends"
        if self.friendship >= REL_FRIEND:
            return "friends"
        if self.friendship >= REL_ACQUAINTANCE:
            return "acquaintances"
        if self.friendship <= -REL_CLOSE:
            return "enemies"
        if self.friendship <= -REL_FRIEND:
            return "rivals"      # new tier between dislike and enemies
        if self.friendship <= -REL_ACQUAINTANCE:
            return "dislike"
        return "strangers"

    def romance_label(self) -> str:
        if self.romance >= 80:
            return "partners"
        if self.romance >= 55:
            return "dating"
        if self.romance >= 30:
            return "crush"
        if self.romance <= -30:
            return "repulsed"
        return "none"

    def add_memory(self, tag: str, valence: float, interaction_id: str = "") -> None:
        self.memories.append({"id": interaction_id, "tag": tag, "valence": valence})
        if len(self.memories) > MAX_MEMORIES:
            self.memories.pop(0)

    def apply_deltas(self, friendship_delta: float, romance_delta: float) -> None:
        self.friendship = max(-100, min(100, self.friendship + friendship_delta))
        self.romance = max(-100, min(100, self.romance + romance_delta))
        self.interactions += 1

    def decay(self) -> None:
        if self.friendship > 0:
            self.friendship = max(0, self.friendship - 0.5)
        if self.romance > 0:
            self.romance = max(0, self.romance - 0.3)


class RelationshipGraph:
    def __init__(self):
        self._pairs: dict[tuple[str, str], RelationshipRecord] = {}

    def _key(self, first: str, second: str) -> tuple[str, str]:
        return (min(first, second), max(first, second))

    def get(self, first: str, second: str) -> RelationshipRecord:
        key = self._key(first, second)
        if key not in self._pairs:
            self._pairs[key] = RelationshipRecord()
        return self._pairs[key]

    def all_pairs(self):
        return list(self._pairs.items())

    def decay_all(self) -> None:
        for record in self._pairs.values():
            record.decay()
