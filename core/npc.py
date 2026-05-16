"""
core/npc.py — Ambient NPC population (Gap 3).

Lightweight NPCs that Sims can encounter at crowded venues without the
overhead of a full Sim profile or LLM adjudication.  Profiles are seeded
from OkCupid essays so each NPC feels distinct.
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from core.relationships import RelationshipGraph

# NPC voice slots pulled from the same pool as Sims
_NPC_VOICES = ["M1", "M2", "M3", "M4", "M5", "F2", "F3", "F4", "F5"]

# Plausible NPC name pool
_NPC_NAMES = [
    "Alex", "Jordan", "Morgan", "Riley", "Casey", "Avery", "Taylor",
    "Jamie", "Reese", "Quinn", "Blake", "Drew", "Rowan", "Sage", "Finley",
]


@dataclass
class NPC:
    npc_id: str
    name: str
    profile_text: str           # OkCupid essay snippet used as persona
    voice_slot: str             # M1-F5
    friendliness: float = 0.5  # 0-1; shapes heuristic outcome


class NPCManager:
    """Spawns and manages short-lived ambient NPCs."""

    def __init__(self):
        self._essay_pool: list[str] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            from datasets.okcupid import load_okcupid_essays
            self._essay_pool = load_okcupid_essays()
        except Exception:
            self._essay_pool = []

    def spawn(self) -> NPC:
        """Create a one-shot NPC, optionally profiled from an OkCupid essay."""
        self._ensure_loaded()
        name = random.choice(_NPC_NAMES)
        profile_text = ""
        if self._essay_pool:
            profile_text = random.choice(self._essay_pool)[:200]
        return NPC(
            npc_id=f"npc_{uuid.uuid4().hex[:6]}",
            name=name,
            profile_text=profile_text,
            voice_slot=random.choice(_NPC_VOICES),
            friendliness=random.uniform(0.3, 0.9),
        )

    def heuristic_interact(
        self,
        sim: "Sim",
        npc: NPC,
        rel_graph: "RelationshipGraph",
    ) -> dict:
        """
        Compute a lightweight interaction outcome without LLM adjudication.
        Returns a result dict compatible with interaction_resolved consumers.
        """
        ocean = sim.ocean
        # Extraversion + agreeableness determine openness to strangers
        receptivity = (ocean["extraversion"] + ocean["agreeableness"]) / 2
        valence = round(npc.friendliness * receptivity + random.uniform(-0.1, 0.1), 2)
        valence = max(-1.0, min(1.0, valence))

        fd = round(valence * 5, 1)   # ±5 friendship shift
        sim.needs.restore("social", max(0, valence * 8))

        emotion = "joy" if valence > 0.5 else ("nervousness" if valence < 0 else "surprise")
        sim.emotion.add(emotion, abs(valence) * 0.5, duration=2, source=f"npc:{npc.name}")

        return {
            "npc_name": npc.name,
            "valence": valence,
            "friendship_delta": fd,
            "emotion": emotion,
            "memory_tag": f"brief encounter with {npc.name} at venue",
        }
