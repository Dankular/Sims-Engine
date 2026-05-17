from __future__ import annotations

from dataclasses import dataclass
import json
import random
from pathlib import Path


@dataclass
class WorldObject:
    object_id: int
    name: str
    type: str
    sub_type: str
    market_price: float
    tradable: bool
    details: dict
    rarity: str
    weight: float
    slot: str


class ObjectManager:
    def __init__(self, catalog_path: str = "datasets/torn_items.json") -> None:
        self.catalog_path = Path(catalog_path)
        self.catalog: dict[int, WorldObject] = {}
        self.lot_objects: dict[str, list[int]] = {}
        self.lot_object_stock: dict[str, dict[int, int]] = {}
        self._load_catalog()

    _RARITY_THRESHOLDS = [
        (50_000, "legendary"),
        (10_000, "epic"),
        (2_000, "rare"),
        (300, "uncommon"),
        (0, "common"),
    ]

    _TYPE_SLOT_MAP = {
        "Weapon": "hand",
        "Armor": "body",
        "Medical": "utility",
        "Temporary": "utility",
        "Drug": "utility",
        "Booster": "utility",
        "Melee": "hand",
        "Primary": "hand",
        "Secondary": "hand",
        "Explosive": "utility",
    }

    _VENUE_TYPE_PREFERENCES = {
        "gym": {"Weapon": 1.2, "Armor": 1.1},
        "library": {"Medical": 1.2, "Temporary": 1.1},
        "restaurant": {"Medical": 1.1, "Temporary": 1.2},
        "nightclub": {"Temporary": 1.2, "Drug": 1.1},
        "hospital": {"Medical": 1.6},
        "police_station": {"Weapon": 1.5, "Armor": 1.4},
        "retail_store": {"Temporary": 1.1, "Medical": 1.1},
        "residential": {"Temporary": 1.2, "Medical": 1.0},
        "community": {"Temporary": 1.0, "Medical": 1.0},
        "business": {"Temporary": 1.1, "Weapon": 1.0},
    }

    def _load_catalog(self) -> None:
        if not self.catalog_path.exists():
            return
        try:
            payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except Exception:
            return
        items = payload.get("items", []) if isinstance(payload, dict) else []
        for item in items:
            try:
                iid = int(item.get("id"))
            except Exception:
                continue
            value = (
                item.get("value", {}) if isinstance(item.get("value", {}), dict) else {}
            )
            self.catalog[iid] = WorldObject(
                object_id=iid,
                name=str(item.get("name", f"item_{iid}")),
                type=str(item.get("type", "Misc")),
                sub_type=str(item.get("sub_type", "")),
                market_price=float(value.get("market_price", 0) or 0),
                tradable=bool(item.get("is_tradable", False)),
                details=item.get("details", {})
                if isinstance(item.get("details", {}), dict)
                else {},
                rarity=self._derive_rarity(float(value.get("market_price", 0) or 0)),
                weight=self._derive_weight(
                    str(item.get("type", "Misc")),
                    float(value.get("market_price", 0) or 0),
                ),
                slot=self._derive_slot(
                    str(item.get("type", "Misc")), str(item.get("sub_type", ""))
                ),
            )

    def _derive_rarity(self, price: float) -> str:
        for threshold, rarity in self._RARITY_THRESHOLDS:
            if price >= threshold:
                return rarity
        return "common"

    def _derive_weight(self, type_name: str, price: float) -> float:
        base = 0.6
        if type_name in {"Weapon", "Armor"}:
            base = 2.2
        elif type_name in {"Medical", "Temporary", "Drug", "Booster"}:
            base = 0.4
        return round(max(0.1, min(8.0, base + (price / 100000.0))), 2)

    def _derive_slot(self, type_name: str, sub_type: str) -> str:
        if sub_type in self._TYPE_SLOT_MAP:
            return self._TYPE_SLOT_MAP[sub_type]
        return self._TYPE_SLOT_MAP.get(type_name, "utility")

    def assign_world_objects(
        self,
        lot_ids: list[str],
        density: int = 8,
        lot_rules: dict[str, dict] | None = None,
    ) -> None:
        if not self.catalog:
            return
        item_ids = list(self.catalog.keys())
        lot_rules = lot_rules or {}
        for lot_id in lot_ids:
            weights = self._lot_item_weights(item_ids, lot_rules.get(lot_id, {}))
            picks = random.choices(
                item_ids, weights=weights, k=min(density, len(item_ids))
            )
            self.lot_objects[lot_id] = picks
            stock: dict[int, int] = {}
            for iid in picks:
                stock[iid] = stock.get(iid, 0) + random.randint(1, 3)
            self.lot_object_stock[lot_id] = stock

    def _lot_item_weights(self, item_ids: list[int], lot_meta: dict) -> list[float]:
        venue = str(
            lot_meta.get("venue_assignment", lot_meta.get("type", "generic"))
        ).lower()
        prefs = self._VENUE_TYPE_PREFERENCES.get(
            venue,
            self._VENUE_TYPE_PREFERENCES.get(
                str(lot_meta.get("type", "generic")).lower(), {}
            ),
        )
        out = []
        for iid in item_ids:
            obj = self.catalog.get(iid)
            if not obj:
                out.append(1.0)
                continue
            wt = 1.0
            wt *= prefs.get(obj.type, 1.0)
            if obj.rarity in {"rare", "epic", "legendary"}:
                wt *= 0.7
            out.append(max(0.05, wt))
        return out

    def assign_sim_inventory(self, sim, count: int = 4) -> None:
        if not self.catalog:
            return
        item_ids = list(self.catalog.keys())
        max_slots = int(getattr(sim, "inventory_max_slots", 12))
        picks = random.sample(item_ids, k=min(count, len(item_ids), max_slots))
        candidate = [self.object_state(iid) for iid in picks]
        sim.inventory_objects = self._apply_inventory_constraints(sim, candidate)
        # Keep legacy string inventory for compatibility
        sim.inventory = [obj["name"] for obj in sim.inventory_objects]

    def _apply_inventory_constraints(self, sim, objects: list[dict]) -> list[dict]:
        max_slots = int(getattr(sim, "inventory_max_slots", 12))
        max_weight = float(getattr(sim, "inventory_max_weight", 24.0))
        by_slot: dict[str, int] = dict(
            getattr(sim, "inventory_slot_limits", {"hand": 2, "body": 1, "utility": 8})
        )
        kept: list[dict] = []
        cur_weight = 0.0
        cur_slot_count: dict[str, int] = {}
        for obj in sorted(
            objects, key=lambda x: x.get("market_price", 0), reverse=True
        ):
            slot = str(obj.get("slot", "utility"))
            weight = float(obj.get("weight", 0.5))
            if len(kept) >= max_slots:
                break
            if cur_weight + weight > max_weight:
                continue
            if cur_slot_count.get(slot, 0) >= by_slot.get(slot, max_slots):
                continue
            kept.append(obj)
            cur_weight += weight
            cur_slot_count[slot] = cur_slot_count.get(slot, 0) + 1
        return kept

    def inventory_weight(self, sim) -> float:
        return round(
            sum(
                float(o.get("weight", 0.0))
                for o in getattr(sim, "inventory_objects", [])
            ),
            2,
        )

    def buy_object(self, sim, lot_id: str, object_id: int, qty: int = 1) -> bool:
        obj = self.catalog.get(object_id)
        stock = self.lot_object_stock.get(lot_id, {})
        if not obj or qty <= 0:
            return False
        if stock.get(object_id, 0) < qty:
            return False
        price = max(1.0, obj.market_price)
        total = price * qty
        if sim.simoleons < total:
            return False
        inv = list(getattr(sim, "inventory_objects", []))
        for _ in range(qty):
            inv.append(self.object_state(object_id))
        constrained = self._apply_inventory_constraints(sim, inv)
        if len(constrained) < len(getattr(sim, "inventory_objects", [])) + qty:
            return False
        sim.simoleons -= total
        sim.inventory_objects = constrained
        sim.inventory = [o["name"] for o in sim.inventory_objects]
        stock[object_id] = max(0, stock.get(object_id, 0) - qty)
        self.lot_object_stock[lot_id] = stock
        return True

    def sell_object(self, sim, object_id: int, qty: int = 1) -> bool:
        if qty <= 0:
            return False
        inv = list(getattr(sim, "inventory_objects", []))
        removed = 0
        kept = []
        for obj in inv:
            if removed < qty and int(obj.get("id", -1)) == object_id:
                removed += 1
                continue
            kept.append(obj)
        if removed < qty:
            return False
        base = self.catalog.get(object_id)
        market = float(base.market_price) if base else 0.0
        payout = max(1.0, market * 0.55) * removed
        sim.simoleons += payout
        sim.inventory_objects = kept
        sim.inventory = [o["name"] for o in sim.inventory_objects]
        return True

    def object_state(self, object_id: int) -> dict:
        obj = self.catalog.get(object_id)
        if not obj:
            return {"id": object_id, "name": f"item_{object_id}", "type": "Misc"}
        return {
            "id": obj.object_id,
            "name": obj.name,
            "type": obj.type,
            "sub_type": obj.sub_type,
            "market_price": round(obj.market_price, 2),
            "tradable": obj.tradable,
            "rarity": obj.rarity,
            "weight": obj.weight,
            "slot": obj.slot,
            "details": dict(obj.details),
        }

    def lot_state(self, lot_id: str) -> list[dict]:
        return [self.object_state(iid) for iid in self.lot_objects.get(lot_id, [])]

    def lot_stock_state(self, lot_id: str) -> list[dict]:
        stock = self.lot_object_stock.get(lot_id, {})
        out = []
        for object_id, qty in stock.items():
            item = self.object_state(int(object_id))
            item["qty"] = int(qty)
            out.append(item)
        out.sort(key=lambda x: (x.get("type", ""), x.get("name", "")))
        return out

    def search_catalog(
        self,
        q: str = "",
        type_filter: str | None = None,
        rarity: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        limit: int = 50,
    ) -> list[dict]:
        needle = q.strip().lower()
        tf = (type_filter or "").strip().lower()
        rf = (rarity or "").strip().lower()
        out: list[dict] = []
        for object_id, obj in self.catalog.items():
            if (
                needle
                and needle not in obj.name.lower()
                and needle not in obj.sub_type.lower()
            ):
                continue
            if tf and tf != obj.type.lower() and tf != obj.sub_type.lower():
                continue
            if rf and rf != obj.rarity.lower():
                continue
            if min_price is not None and obj.market_price < float(min_price):
                continue
            if max_price is not None and obj.market_price > float(max_price):
                continue
            out.append(self.object_state(object_id))
            if len(out) >= max(1, min(int(limit), 500)):
                break
        out.sort(key=lambda x: x.get("market_price", 0.0))
        return out
