"""
world/crafting.py — Skill-based crafting outputs.

Skills produce tangible items that enter the sim's inventory, can be
gifted to other sims, sold for simoleons, or consumed directly.

  cooking   → FoodItem   (hunger restore + quality; sells for 10-80§)
  creativity → Artwork    (sell value; creative_reputation bonus on sale)
  writing   → Manuscript  (royalty income per tick while "published")
  logic     → Invention   (one-time simoleon payout + logic XP)

CraftingEngine.tick() is called from engine.run_tick() every tick.
Crafting fires probabilistically based on skill level and need pressure.
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine

# ── Item types ─────────────────────────────────────────────────────────────────

@dataclass
class CraftedItem:
    item_id: str
    item_type: str          # "food", "artwork", "manuscript", "invention"
    name: str
    quality: float          # 0.0..1.0 — driven by skill level
    sell_value: float       # simoleons on sale
    creator_id: str
    created_tick: int
    # Type-specific
    hunger_restore: float   = 0.0   # food only
    royalty_per_tick: float = 0.0   # manuscript only
    published: bool         = False  # manuscript only
    consumed: bool          = False


def _quality_from_skill(level: float) -> float:
    """Skill 0→10 maps to quality 0.1→1.0 with some random variance."""
    base = min(1.0, level / 10.0)
    variance = random.uniform(-0.1, 0.1)
    return max(0.05, min(1.0, base + variance))


# ── Crafting logic per skill ────────────────────────────────────────────────────

def _craft_food(sim: "Sim", tick: int) -> CraftedItem | None:
    level = sim.skills.levels.get("cooking", 0)
    if level < 1:
        return None
    q = _quality_from_skill(level)
    names = ["simple salad", "pasta dish", "gourmet meal", "chef's tasting plate"]
    name  = names[min(int(q * len(names)), len(names) - 1)]
    return CraftedItem(
        item_id       = uuid.uuid4().hex[:8],
        item_type     = "food",
        name          = name,
        quality       = q,
        sell_value    = round(10 + q * 70, 2),
        creator_id    = sim.sim_id,
        created_tick  = tick,
        hunger_restore= round(20 + q * 60, 1),
    )


def _craft_artwork(sim: "Sim", tick: int) -> CraftedItem | None:
    level = sim.skills.levels.get("creativity", 0)
    if level < 1:
        return None
    q = _quality_from_skill(level)
    styles = ["sketch", "acrylic painting", "watercolour", "digital art", "oil masterpiece"]
    name   = random.choice(styles[:max(1, int(q * len(styles) + 1))])
    return CraftedItem(
        item_id      = uuid.uuid4().hex[:8],
        item_type    = "artwork",
        name         = name,
        quality      = q,
        sell_value   = round(20 + q * 180, 2),
        creator_id   = sim.sim_id,
        created_tick = tick,
    )


def _craft_manuscript(sim: "Sim", tick: int) -> CraftedItem | None:
    # Writing uses creativity skill
    level = sim.skills.levels.get("creativity", 0)
    if level < 2:
        return None
    q = _quality_from_skill(level)
    genres = ["short story", "novella", "thriller", "literary novel", "bestseller"]
    name   = genres[min(int(q * len(genres)), len(genres) - 1)]
    return CraftedItem(
        item_id         = uuid.uuid4().hex[:8],
        item_type       = "manuscript",
        name            = name,
        quality         = q,
        sell_value      = round(30 + q * 120, 2),
        creator_id      = sim.sim_id,
        created_tick    = tick,
        royalty_per_tick= round(q * 5, 2),   # passive income per tick when published
        published       = True,
    )


def _craft_invention(sim: "Sim", tick: int) -> CraftedItem | None:
    level = sim.skills.levels.get("logic", 0)
    if level < 3:
        return None
    q = _quality_from_skill(level)
    types = ["gadget", "prototype", "patent-worthy device", "revolutionary invention"]
    name  = types[min(int(q * len(types)), len(types) - 1)]
    return CraftedItem(
        item_id      = uuid.uuid4().hex[:8],
        item_type    = "invention",
        name         = name,
        quality      = q,
        sell_value   = round(50 + q * 300, 2),
        creator_id   = sim.sim_id,
        created_tick = tick,
    )


_CRAFT_FN = {
    "cooking":    (_craft_food,       "cooking"),
    "creativity": (_craft_artwork,    "making art"),
    "writing":    (_craft_manuscript, "writing"),
    "logic":      (_craft_invention,  "inventing"),
}

MAX_INVENTORY_ITEMS = 12
CRAFT_BASE_CHANCE   = 0.08   # per tick per skill when conditions met


# ── CraftingEngine ─────────────────────────────────────────────────────────────

class CraftingEngine:

    def tick(self, engine: "SimEngine") -> None:
        tick = engine.tick_count
        for sim in engine.sims:
            if getattr(sim, "_sleeping", False):
                continue
            self._maybe_craft(sim, tick, engine)
            self._collect_royalties(sim, tick)

    def _maybe_craft(self, sim: "Sim", tick: int, engine: "SimEngine") -> None:
        inventory: list[CraftedItem] = getattr(sim, "crafted_inventory", [])
        if len(inventory) >= MAX_INVENTORY_ITEMS:
            return

        for skill_key, (fn, activity_label) in _CRAFT_FN.items():
            level = sim.skills.levels.get(skill_key, 0)
            if level < 1:
                continue
            # Chance rises with skill level and fun need pressure
            fun_pressure = max(0.0, (80 - sim.needs.fun) / 80)
            chance = CRAFT_BASE_CHANCE * (level / 5.0) * (1 + fun_pressure)
            if random.random() > chance:
                continue

            item = fn(sim, tick)
            if item is None:
                continue

            if not hasattr(sim, "crafted_inventory"):
                sim.crafted_inventory = []
            sim.crafted_inventory.append(item)

            # XP reward for the act of crafting
            sim.skills.gain_xp(skill_key, round(item.quality * 1.5, 2))
            sim.needs.restore("fun", 8.0 * item.quality)
            sim.emotion.add("pride", 0.6, duration=4, source=f"crafted:{item.item_type}")

            # Artwork boosts creative reputation immediately
            if item.item_type == "artwork":
                sim.creative_reputation = min(100, sim.creative_reputation + item.quality * 5)

            engine._bus.emit(
                "item_crafted",
                sim=sim,
                item_type=item.item_type,
                item_name=item.name,
                quality=round(item.quality, 2),
                sell_value=item.sell_value,
                tick=tick,
            )
            break   # one craft per sim per tick

    def _collect_royalties(self, sim: "Sim", tick: int) -> None:
        inventory = getattr(sim, "crafted_inventory", [])
        for item in inventory:
            if item.item_type == "manuscript" and item.published and not item.consumed:
                sim.simoleons += item.royalty_per_tick

    @staticmethod
    def sell_item(sim: "Sim", item_id: str, engine: "SimEngine") -> float:
        """Sell a crafted item for its sell_value. Returns amount earned."""
        inventory = getattr(sim, "crafted_inventory", [])
        for item in inventory:
            if item.item_id == item_id and not item.consumed:
                item.consumed = True
                sim.simoleons += item.sell_value
                sim.emotion.add("joy", 0.5, duration=3, source="sold_item")
                engine._bus.emit("item_sold", sim=sim, item=item, tick=engine.tick_count)
                return item.sell_value
        return 0.0

    @staticmethod
    def consume_food(sim: "Sim", engine: "SimEngine") -> bool:
        """Eat the best available food item. Returns True if consumed."""
        inventory = getattr(sim, "crafted_inventory", [])
        foods = [i for i in inventory if i.item_type == "food" and not i.consumed]
        if not foods:
            return False
        best = max(foods, key=lambda i: i.quality)
        best.consumed = True
        sim.needs.restore("hunger", best.hunger_restore)
        sim.emotion.add("joy", best.quality * 0.4, duration=3, source="ate_crafted_food")
        return True
