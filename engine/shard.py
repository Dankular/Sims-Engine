"""
engine/shard.py — Shard-based simulation topology.

Each shard owns a set of sim_ids for a given zone (lot/venue/neighborhood).
Cross-shard interactions route through the EventBus as async events rather
than synchronous pair adjudication, so tick cost stays bounded per shard.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from engine.events import EventBus

logger = logging.getLogger(__name__)


@dataclass
class Shard:
    shard_id: str       # e.g. lot_id, "global"
    zone_type: str      # "lot", "venue", "neighborhood"
    sim_ids: set[str] = field(default_factory=set)

    def add(self, sim_id: str) -> None:
        self.sim_ids.add(sim_id)

    def remove(self, sim_id: str) -> None:
        self.sim_ids.discard(sim_id)

    def owns(self, sim_id: str) -> bool:
        return sim_id in self.sim_ids


class ShardManager:
    """
    Routes sims to authoritative shards by location.

    Cross-shard interactions are emitted as async events on the EventBus.
    Each shard handles its own sims; only state diffs cross shard boundaries.
    """

    def __init__(self, bus: "EventBus") -> None:
        self._shards: dict[str, Shard] = {}
        self._sim_to_shard: dict[str, str] = {}
        self._bus = bus
        self._cross_shard_handlers: list[Callable[[dict], None]] = []
        self._get_or_create("global", "venue")

    def _get_or_create(self, shard_id: str, zone_type: str = "lot") -> Shard:
        if shard_id not in self._shards:
            self._shards[shard_id] = Shard(shard_id=shard_id, zone_type=zone_type)
        return self._shards[shard_id]

    def assign(self, sim_id: str, shard_id: str, zone_type: str = "lot") -> bool:
        """Move a sim to a shard. Returns True if the shard changed."""
        old = self._sim_to_shard.get(sim_id)
        if old == shard_id:
            return False
        if old and old in self._shards:
            self._shards[old].remove(sim_id)
        self._get_or_create(shard_id, zone_type).add(sim_id)
        self._sim_to_shard[sim_id] = shard_id
        return True

    def get_shard_id(self, sim_id: str) -> str:
        return self._sim_to_shard.get(sim_id, "global")

    def same_shard(self, sim_a_id: str, sim_b_id: str) -> bool:
        return self._sim_to_shard.get(sim_a_id) == self._sim_to_shard.get(sim_b_id)

    def emit_cross_shard(
        self, sim_a_id: str, sim_b_id: str, interaction: str, payload: dict
    ) -> None:
        """Async cross-shard interaction — receiving shard adjudicates later."""
        event = {
            "initiator_id": sim_a_id,
            "target_id": sim_b_id,
            "interaction": interaction,
            "target_shard": self._sim_to_shard.get(sim_b_id, "global"),
            "payload": payload,
        }
        self._bus.emit("cross_shard_interaction", **event)
        for handler in self._cross_shard_handlers:
            try:
                handler(event)
            except Exception:
                pass

    def on_cross_shard(self, handler: Callable[[dict], None]) -> None:
        self._cross_shard_handlers.append(handler)

    def sims_in_shard(self, shard_id: str) -> set[str]:
        shard = self._shards.get(shard_id)
        return set(shard.sim_ids) if shard else set()

    def all_shards(self) -> list[Shard]:
        return list(self._shards.values())

    def sim_count(self) -> dict[str, int]:
        return {sid: len(s.sim_ids) for sid, s in self._shards.items()}
