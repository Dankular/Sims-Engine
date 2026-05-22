"""world/civilization.py — Research, culture, and technology progression."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)

__all__ = ["TechNode", "CivilizationSystem"]

RESEARCH_FROM_INTELLECTUAL = 2.0
RESEARCH_FROM_DEEP = 0.5
CULTURE_FROM_CREATIVE = 1.5
CULTURE_FROM_SOCIAL = 0.3
TIER_THRESHOLDS = [0, 50, 150, 350, 700]


@dataclass
class TechNode:
    node_id: str
    name: str
    tier: int
    prereqs: list[str]
    research_cost: float
    description: str
    shop_cost_modifier: float = 0.0
    new_interactions: list[str] = field(default_factory=list)
    culture_multiplier: float = 1.0
    unlocked: bool = False
    unlock_tick: int = 0


def _build_tech_tree() -> dict[str, TechNode]:
    nodes = [
        TechNode("writing",         "Writing",          0, [],             10,  "Record-keeping and storytelling",
                 new_interactions=["read aloud", "share manuscript"]),
        TechNode("agriculture",     "Agriculture",      0, [],             15,  "Reliable food production",
                 shop_cost_modifier=-0.05),
        TechNode("trade",           "Trade Networks",   1, ["writing"],    40,  "Exchange of goods across distances",
                 shop_cost_modifier=-0.05),
        TechNode("medicine",        "Medicine",         1, ["agriculture"], 45, "Healing arts and illness prevention"),
        TechNode("education",       "Formal Education", 1, ["writing"],    50,  "Structured skill transmission"),
        TechNode("printing",        "Printing Press",   2, ["writing", "trade"], 100, "Mass information spread",
                 new_interactions=["distribute pamphlet", "share broadsheet"]),
        TechNode("industrialization", "Industrialization", 2, ["trade", "education"], 120, "Mechanised production",
                 shop_cost_modifier=-0.10),
        TechNode("telecommunications", "Telecommunications", 3, ["printing", "industrialization"], 200,
                 "Instant long-distance communication",
                 new_interactions=["video_call", "send meme", "send voice note"]),
        TechNode("democracy",       "Democracy",        3, ["printing", "education"], 180,
                 "Representative governance",
                 new_interactions=["cast vote", "campaign speech", "debate policy"]),
        TechNode("psychology",      "Psychology",       3, ["education", "medicine"], 220,
                 "Understanding of the mind",
                 new_interactions=["offer therapy", "do personality test"]),
        TechNode("automation",      "Automation",       4, ["industrialization", "telecommunications"], 400,
                 "Labour-saving machinery", shop_cost_modifier=-0.15),
        TechNode("digital_art",     "Digital Art",      4, ["telecommunications"], 350,
                 "Creative expression via technology",
                 new_interactions=["share digital artwork", "co-create online"],
                 culture_multiplier=1.5),
        TechNode("ai_assistance",   "AI Assistance",    4, ["automation", "psychology"], 500,
                 "Intelligent tools augmenting every field",
                 shop_cost_modifier=-0.10,
                 new_interactions=["consult AI advisor", "share AI creation"]),
    ]
    return {n.node_id: n for n in nodes}


class CivilizationSystem:
    TECH_CHECK_INTERVAL = 10

    def __init__(self) -> None:
        self.tech_tree: dict[str, TechNode] = _build_tech_tree()
        self.research_points: float = 0.0
        self.culture_points: float = 0.0
        self.current_tier: int = 0
        self._unlocked_interactions: set[str] = set()
        self._culture_multiplier: float = 1.0

    def available_interaction_types(self) -> set[str]:
        return set(self._unlocked_interactions)

    def summary(self) -> dict:
        unlocked = [n.name for n in self.tech_tree.values() if n.unlocked]
        next_nodes = [
            {"id": n.node_id, "name": n.name, "cost": n.research_cost, "tier": n.tier}
            for n in self.tech_tree.values()
            if not n.unlocked and self._prereqs_met(n)
        ]
        return {
            "tier": self.current_tier,
            "research_points": round(self.research_points, 1),
            "culture_points": round(self.culture_points, 1),
            "unlocked_count": len(unlocked),
            "unlocked": unlocked,
            "next_available": next_nodes[:5],
            "available_interactions": sorted(self._unlocked_interactions),
        }

    def on_interaction_resolved(
        self, interaction: str, valence: float, engine: "SimEngine"
    ) -> None:
        lc = interaction.lower()
        if any(k in lc for k in ("debate", "philosophy", "challenge", "discuss", "intellectual")):
            self.research_points += RESEARCH_FROM_INTELLECTUAL
        if any(k in lc for k in ("secret", "confide", "life advice", "fear", "deep")):
            self.research_points += RESEARCH_FROM_DEEP
        if any(k in lc for k in ("creative", "art", "music", "craft", "cook", "programme", "write")):
            self.culture_points += CULTURE_FROM_CREATIVE * self._culture_multiplier
        if any(k in lc for k in ("chat", "story", "celebrate", "joke", "compliment")):
            self.culture_points += CULTURE_FROM_SOCIAL

    def tick(self, engine: "SimEngine") -> None:
        if engine._tick_count % self.TECH_CHECK_INTERVAL != 0:
            return

        for node in self.tech_tree.values():
            if node.unlocked:
                continue
            if self._can_unlock(node):
                node.unlocked = True
                node.unlock_tick = engine._tick_count
                self._apply_tech_effects(node, engine)
                engine._bus.emit(
                    "tech_unlocked",
                    node_id=node.node_id,
                    name=node.name,
                    tier=node.tier,
                    tick=engine._tick_count,
                )
                try:
                    engine.world_history.record(
                        tick=engine._tick_count,
                        event_type="tech_unlocked",
                        description=f"Civilization unlocked: {node.name}. {node.description}",
                        participants=[],
                        location="global",
                        impact=0.4 + node.tier * 0.1,
                        tags=["technology", "milestone"],
                    )
                except Exception:
                    pass
                logger.info("[Civilization] Unlocked tier-%d tech: %s", node.tier, node.name)

        # Update current tier
        new_tier = 0
        for t, threshold in enumerate(TIER_THRESHOLDS):
            if self.research_points >= threshold:
                new_tier = t
        if new_tier > self.current_tier:
            old = self.current_tier
            self.current_tier = new_tier
            engine._bus.emit("civilization_tier_up", old_tier=old, new_tier=new_tier, tick=engine._tick_count)

        # Refresh culture multiplier
        self._culture_multiplier = 1.0
        for n in self.tech_tree.values():
            if n.unlocked and n.culture_multiplier > 1.0:
                self._culture_multiplier = max(self._culture_multiplier, n.culture_multiplier)

    def _prereqs_met(self, node: TechNode) -> bool:
        return all(
            self.tech_tree.get(p, TechNode("", "", 0, [], 9999, "")).unlocked
            for p in node.prereqs
        )

    def _can_unlock(self, node: TechNode) -> bool:
        return self._prereqs_met(node) and self.research_points >= node.research_cost

    def _apply_tech_effects(self, node: TechNode, engine: "SimEngine") -> None:
        for interaction in node.new_interactions:
            self._unlocked_interactions.add(interaction)
        if node.shop_cost_modifier != 0.0:
            try:
                pi = engine.macro_economy.goods.price_index
                engine.macro_economy.goods.price_index = max(
                    0.5, min(2.0, pi + node.shop_cost_modifier)
                )
            except Exception:
                pass
