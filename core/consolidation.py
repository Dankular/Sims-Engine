"""
core/consolidation.py — Sleep-phase memory consolidation (System 5).

During low-energy sleep ticks, the sim's highest-valence short-term memories
are compressed into a long-term narrative entry stored back into the MemoryStore
and the SQLite events table.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from core.memory import MemoryStore

logger = logging.getLogger(__name__)

CONSOLIDATION_ENERGY_THRESHOLD = 15    # sim must be asleep (energy < this)
CONSOLIDATION_MIN_MEMORIES     = 5     # minimum entries needed to consolidate
CONSOLIDATION_TICK_INTERVAL    = 20    # minimum ticks between consolidations
CONSOLIDATION_TOP_K            = 8     # how many memories to compress


def consolidate_memories(
    sim: "Sim",
    memory_store: "MemoryStore",
    tick: int,
) -> str | None:
    """
    Compress the sim's most emotionally significant short-term memories into
    a single long-term narrative entry.  Returns the consolidated text, or None.
    """
    if sim.needs.energy > CONSOLIDATION_ENERGY_THRESHOLD:
        return None

    last = getattr(sim, "_last_consolidation_tick", -9999)
    if (tick - last) < CONSOLIDATION_TICK_INTERVAL:
        return None

    # Collect all pair-memories that involve this sim
    all_memories: list[dict] = []
    for pair_key, entries in memory_store._store.items():
        parts = pair_key.split("_", 1)
        if len(parts) == 2 and sim.sim_id in parts:
            all_memories.extend(entries)

    if len(all_memories) < CONSOLIDATION_MIN_MEMORIES:
        return None

    # Keep the CONSOLIDATION_TOP_K most emotionally significant
    all_memories.sort(key=lambda m: abs(m.get("valence", 0.0)), reverse=True)
    top = all_memories[:CONSOLIDATION_TOP_K]

    parts: list[str] = []
    for m in top:
        v = m.get("valence", 0.0)
        polarity = "positive" if v > 0 else "negative"
        text = m.get("text", m.get("tag", "unknown"))
        parts.append(f"[{polarity}:{v:+.2f}] {text}")

    consolidated = (
        f"Consolidated memory (tick {tick}): "
        + " | ".join(parts)
    )

    # Write back into the long-term store
    memory_store.write_long_term(
        sim_id=sim.sim_id,
        text=consolidated,
        tag="consolidation",
        valence=0.0,
        tick=tick,
    )

    sim._last_consolidation_tick = tick
    logger.debug("[CONSOLIDATION] %s: %d memories → long-term", sim.name, len(top))
    return consolidated
