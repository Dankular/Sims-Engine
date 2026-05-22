"""
world/lot_layout.py — Home object placement system.

Sims can move items from their inventory into rooms/zones of their household lot.
Placed items provide passive ambient effects each tick (much smaller than consuming them).

Zones:  living_room, bedroom, kitchen, bathroom, garage, garden, study
Key:    lot_id == sim.household_id
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim

# ── Zone definitions ──────────────────────────────────────────────────────────

ZONES = ["living_room", "bedroom", "kitchen", "bathroom", "garage", "garden", "study"]

ZONE_CAPACITY = 8  # max items per zone

# Which item types are allowed in each zone
ZONE_ALLOWED_TYPES: dict[str, set[str]] = {
    "living_room": {
        "Furniture",
        "Collectible",
        "Book",
        "Flower",
        "Plushie",
        "Special",
        "Artifact",
        "Jewelry",
        "Temporary",
        "Pet",
        "Pet Supply",
    },
    "bedroom": {
        "Furniture",
        "Clothing",
        "Plushie",
        "Book",
        "Jewelry",
        "Special",
        "Medical",
    },
    "kitchen": {
        "Tool",
        "Supply Pack",
        "Candy",
        "Alcohol",
        "Medical",
        "Energy Drink",
        "Enhancer",
        "Pet",
        "Pet Supply",
    },
    "bathroom": {"Medical", "Tool", "Flower", "Clothing", "Special"},
    "garage": {"Car", "Weapon", "Tool", "Armor", "Explosive", "Material", "Booster"},
    "garden": {"Flower", "Collectible", "Artifact", "Furniture", "Special", "Tool"},
    "study": {
        "Book",
        "Artifact",
        "Collectible",
        "Tool",
        "Jewelry",
        "Special",
        "Computer",
    },
}

# "Other" and unlisted types are allowed anywhere (catch-all)
_OPEN_TYPES = {"Other", "Unused", "Misc"}

# ── Passive effect tables ─────────────────────────────────────────────────────

# Base passive need contribution per tick for each item type
_TYPE_PASSIVE: dict[str, dict[str, float]] = {
    "Furniture": {"comfort": 0.30, "environment": 0.15},
    "Collectible": {"fun": 0.15, "environment": 0.10},
    "Book": {"fun": 0.10},
    "Flower": {"environment": 0.20, "comfort": 0.05},
    "Plushie": {"comfort": 0.15},
    "Car": {"fun": 0.10},
    "Tool": {"fun": 0.05},
    "Medical": {"comfort": 0.10},
    "Jewelry": {"comfort": 0.05, "environment": 0.10},
    "Artifact": {"fun": 0.12, "environment": 0.08},
    "Clothing": {"comfort": 0.08},
    "Alcohol": {"fun": 0.05},
    "Candy": {"fun": 0.05},
    "Supply Pack": {"hunger": 0.05},
    "Energy Drink": {"energy": 0.05},
    "Enhancer": {"energy": 0.08},
    "Special": {"fun": 0.10, "environment": 0.10},
    "Furniture": {"comfort": 0.30, "environment": 0.15},
}

# Extra need bonus per zone type (multiplied onto the item contribution)
_ZONE_NEED_FOCUS: dict[str, dict[str, float]] = {
    "living_room": {"comfort": 1.4, "social": 0.2, "fun": 1.2},
    "bedroom": {"comfort": 1.2, "energy": 0.3},
    "kitchen": {"hunger": 1.5, "hygiene": 0.5},
    "bathroom": {"hygiene": 1.5, "comfort": 1.1},
    "garage": {"fun": 1.3},
    "garden": {"environment": 1.6, "fun": 1.1},
    "study": {"fun": 1.3},
}

_RARITY_MULT: dict[str, float] = {
    "common": 1.0,
    "uncommon": 1.2,
    "rare": 1.5,
    "epic": 2.0,
    "legendary": 3.0,
}


# ── LotLayout ─────────────────────────────────────────────────────────────────


class LotLayout:
    def __init__(self) -> None:
        # lot_id → zone → list[item_dict]
        self._placements: dict[str, dict[str, list[dict]]] = {}

    def _lot(self, lot_id: str) -> dict[str, list[dict]]:
        if lot_id not in self._placements:
            self._placements[lot_id] = {z: [] for z in ZONES}
        return self._placements[lot_id]

    # ── Placement ─────────────────────────────────────────────────────────────

    def place(self, lot_id: str, zone: str, item: dict) -> dict:
        """
        Move an item into a zone of the lot.
        Returns {"ok": True} or {"ok": False, "reason": ...}.
        The caller is responsible for removing the item from sim.inventory_objects.
        """
        if zone not in ZONES:
            return {"ok": False, "reason": f"unknown zone '{zone}'; valid: {ZONES}"}

        item_type = str(item.get("type", "Other"))
        allowed = ZONE_ALLOWED_TYPES.get(zone, set()) | _OPEN_TYPES
        if item_type not in allowed and item_type not in _OPEN_TYPES:
            return {
                "ok": False,
                "reason": f"{item_type} items cannot be placed in {zone}",
            }

        lot = self._lot(lot_id)
        if len(lot[zone]) >= ZONE_CAPACITY:
            return {
                "ok": False,
                "reason": f"{zone} is full ({ZONE_CAPACITY} items max)",
            }

        lot[zone].append(dict(item))
        return {"ok": True}

    def remove(self, lot_id: str, object_id: int) -> dict:
        """
        Remove a placed item by object_id from any zone.
        Returns {"ok": True, "item": {...}, "zone": ...} or {"ok": False, "reason": ...}.
        The caller is responsible for returning the item to sim.inventory_objects.
        """
        lot = self._lot(lot_id)
        for zone, items in lot.items():
            for i, item in enumerate(items):
                if int(item.get("id", -1)) == int(object_id):
                    removed = items.pop(i)
                    return {"ok": True, "item": removed, "zone": zone}
        return {"ok": False, "reason": "item not found in lot"}

    # ── Read ──────────────────────────────────────────────────────────────────

    def layout(self, lot_id: str) -> dict:
        """Full placement map with per-item details."""
        lot = self._lot(lot_id)
        out: dict[str, list[dict]] = {}
        for zone in ZONES:
            items = lot.get(zone, [])
            out[zone] = [
                {**item, "passive_per_tick": _passive_for(item, zone)} for item in items
            ]
        return {
            "lot_id": lot_id,
            "zones": out,
            "total_placed": sum(len(v) for v in lot.values()),
            "zone_capacity": ZONE_CAPACITY,
        }

    def ambiance(self, lot_id: str) -> dict:
        """Aggregate passive contribution all placed items give per tick."""
        lot = self._lot(lot_id)
        totals: dict[str, float] = {}
        by_zone: dict[str, dict] = {}
        for zone in ZONES:
            zone_totals: dict[str, float] = {}
            for item in lot.get(zone, []):
                for need, val in _passive_for(item, zone).items():
                    zone_totals[need] = round(zone_totals.get(need, 0.0) + val, 3)
                    totals[need] = round(totals.get(need, 0.0) + val, 3)
            by_zone[zone] = {
                "item_count": len(lot.get(zone, [])),
                "per_tick": zone_totals,
            }
        return {
            "lot_id": lot_id,
            "by_zone": by_zone,
            "totals_per_tick": totals,
            "total_placed": sum(len(v) for v in lot.values()),
        }

    # ── Engine tick ───────────────────────────────────────────────────────────

    def tick_passive_effects(self, sim: "Sim", lot_id: str) -> None:
        """Apply ambient home-object bonuses to a sim each tick."""
        lot = self._placements.get(lot_id)
        if not lot:
            return
        for zone, items in lot.items():
            for item in items:
                for need, val in _passive_for(item, zone).items():
                    if hasattr(sim.needs, need):
                        cur = float(getattr(sim.needs, need))
                        setattr(sim.needs, need, min(100.0, cur + val))


# ── Helper ────────────────────────────────────────────────────────────────────


def _passive_for(item: dict, zone: str) -> dict[str, float]:
    """Compute per-tick passive contribution of one placed item in a zone."""
    type_name = str(item.get("type", "Other"))
    rarity = str(item.get("rarity", "common"))
    mult = _RARITY_MULT.get(rarity, 1.0)
    focus = _ZONE_NEED_FOCUS.get(zone, {})

    base = dict(_TYPE_PASSIVE.get(type_name, {"fun": 0.05}))
    out: dict[str, float] = {}
    for need, val in base.items():
        zone_factor = focus.get(need, 1.0)
        out[need] = round(val * mult * zone_factor, 3)
    # Zone focus can add needs not in the base passive (e.g. social from living_room)
    for need, factor in focus.items():
        if need not in out and factor > 0.0:
            out[need] = round(0.05 * mult * factor, 3)
    return {k: v for k, v in out.items() if v > 0}
