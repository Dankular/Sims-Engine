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
        self.lot_rules: dict[str, dict] = {}
        self._price_multipliers: dict[tuple[str, int], float] = {}
        self._sale_events: dict[str, dict] = {}
        self._last_sale_tick: int = -1
        self._use_counts: dict[str, int] = {}
        self._buy_counts: dict[str, int] = {}
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
        "Clothing": "body",
        "Tool": "hand",
        "Medical": "utility",
        "Temporary": "utility",
        "Drug": "utility",
        "Booster": "utility",
        "Melee": "hand",
        "Primary": "hand",
        "Secondary": "hand",
        "Explosive": "utility",
    }

    _TYPE_WEIGHT_BASE: dict[str, float] = {
        "Weapon": 2.2,
        "Armor": 2.0,
        "Clothing": 0.6,
        "Jewelry": 0.2,
        "Medical": 0.4,
        "Temporary": 0.4,
        "Drug": 0.4,
        "Booster": 0.4,
        "Candy": 0.2,
        "Flower": 0.2,
        "Plushie": 0.3,
        "Book": 0.5,
        "Collectible": 0.5,
        "Artifact": 0.5,
        "Tool": 1.0,
        "Explosive": 0.8,
        "Alcohol": 0.5,
        "Energy Drink": 0.3,
        "Enhancer": 0.3,
        "Car": 0.6,
        "Supply Pack": 0.8,
        "Furniture": 3.0,
        "Material": 0.7,
        "Special": 0.5,
    }

    _VENUE_TYPE_PREFERENCES = {
        "gym": {
            "Weapon": 1.2,
            "Armor": 1.1,
            "Enhancer": 1.3,
            "Energy Drink": 1.2,
        },
        "library": {
            "Medical": 1.2,
            "Temporary": 1.1,
            "Book": 1.6,
            "Artifact": 1.3,
            "Collectible": 1.2,
        },
        "restaurant": {
            "Medical": 1.1,
            "Temporary": 1.2,
            "Alcohol": 1.3,
            "Candy": 1.2,
        },
        "nightclub": {
            "Temporary": 1.2,
            "Drug": 1.1,
            "Alcohol": 1.6,
            "Energy Drink": 1.3,
        },
        "hospital": {
            "Medical": 1.6,
            "Supply Pack": 1.3,
        },
        "police_station": {"Weapon": 1.5, "Armor": 1.4},
        "retail_store": {
            "Temporary": 1.1,
            "Medical": 1.1,
            "Clothing": 1.5,
            "Jewelry": 1.4,
            "Collectible": 1.2,
        },
        "residential": {
            "Temporary": 1.2,
            "Medical": 1.0,
            "Furniture": 1.5,
            "Plushie": 1.2,
            "Book": 1.3,
        },
        "community": {
            "Temporary": 1.0,
            "Medical": 1.0,
            "Book": 1.2,
            "Flower": 1.3,
        },
        "business": {
            "Temporary": 1.1,
            "Weapon": 1.0,
            "Enhancer": 1.4,
            "Jewelry": 1.2,
        },
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
        base = self._TYPE_WEIGHT_BASE.get(type_name, 0.6)
        return round(max(0.1, min(8.0, base + (price / 100_000.0))), 2)

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
        self.lot_rules.update(lot_rules)
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
            for iid in stock.keys():
                self._price_multipliers[(lot_id, int(iid))] = random.uniform(0.9, 1.2)

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
        focus_types = {
            str(t).strip().lower()
            for t in lot_meta.get("focus_types", [])
            if str(t).strip()
        }
        strict_focus = bool(lot_meta.get("strict_focus", False)) and bool(focus_types)
        for iid in item_ids:
            obj = self.catalog.get(iid)
            if not obj:
                out.append(1.0)
                continue
            wt = 1.0
            if strict_focus:
                if str(obj.type).strip().lower() in focus_types:
                    wt *= 3.8
                else:
                    wt *= 0.000001
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
        price = self.current_price(lot_id, object_id)
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
        self._adjust_demand(lot_id, object_id, up=True)
        t = str(obj.type).lower()
        self._buy_counts[t] = self._buy_counts.get(t, 0) + int(qty)
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
        _eng = getattr(sim, '_engine_ref', None)
        if _eng:
            from persistence.ledger import TX_OBJECT_SALE
            _eng._tx(sim, payout, TX_OBJECT_SALE, description='item sold')
        else:
            sim.simoleons += payout
        sim.inventory_objects = kept
        sim.inventory = [o["name"] for o in sim.inventory_objects]
        return True

    def use_object(self, sim, object_id: int) -> dict:
        inv = list(getattr(sim, "inventory_objects", []))
        selected = None
        kept = []
        consumed = False
        for item in inv:
            if not consumed and int(item.get("id", -1)) == int(object_id):
                selected = dict(item)
                consumed = True
                continue
            kept.append(item)
        if selected is None:
            return {"ok": False, "reason": "item_not_found"}

        effect = self._effect_for_item(selected)
        self._apply_item_effect(sim, effect, selected)
        t = str(selected.get("type", "")).lower()
        self._use_counts[t] = self._use_counts.get(t, 0) + 1
        sim.inventory_objects = kept
        sim.inventory = [o["name"] for o in sim.inventory_objects]
        return {"ok": True, "item": selected, "effect": effect}

    def current_price(self, lot_id: str, object_id: int) -> float:
        obj = self.catalog.get(int(object_id))
        if not obj:
            return 1.0
        base = max(1.0, float(obj.market_price))
        mult = float(self._price_multipliers.get((lot_id, int(object_id)), 1.0))
        sale_mult = self._sale_multiplier(lot_id, int(object_id), obj)
        return round(max(1.0, base * mult * sale_mult), 2)

    def tick_market(self, tick: int) -> None:
        for key, val in list(self._price_multipliers.items()):
            drift = random.uniform(-0.02, 0.02)
            self._price_multipliers[key] = max(0.6, min(1.8, val + drift))

        if self._last_sale_tick < 0 or (tick - self._last_sale_tick) >= 24:
            self._roll_sale_event(tick)
        # Type-level adaptive economy pressure
        if tick % 12 == 0 and self._buy_counts:
            hot_types = sorted(
                self._buy_counts.items(), key=lambda x: x[1], reverse=True
            )[:3]
            for lot_id, stock in self.lot_object_stock.items():
                for oid in list(stock.keys()):
                    obj = self.catalog.get(int(oid))
                    if not obj:
                        continue
                    if any(str(obj.type).lower() == t for t, _ in hot_types):
                        key = (lot_id, int(oid))
                        self._price_multipliers[key] = min(
                            2.2, self._price_multipliers.get(key, 1.0) + 0.03
                        )

    def _roll_sale_event(self, tick: int) -> None:
        self._last_sale_tick = tick
        self._sale_events.clear()
        if not self.lot_object_stock:
            return
        lots = list(self.lot_object_stock.keys())
        lot_id = random.choice(lots)
        sale_kind = random.choice(["type", "rarity", "all"])
        event = {
            "name": "Daily Deal",
            "discount": random.choice([0.7, 0.75, 0.8, 0.85]),
            "kind": sale_kind,
            "target": "",
            "expires_tick": tick + 24,
        }
        if sale_kind in {"type", "rarity"}:
            stock_ids = [int(i) for i in self.lot_object_stock.get(lot_id, {}).keys()]
            objs = [self.catalog.get(i) for i in stock_ids if self.catalog.get(i)]
            if objs:
                if sale_kind == "type":
                    event["target"] = random.choice(objs).type
                else:
                    event["target"] = random.choice(objs).rarity
        self._sale_events[lot_id] = event

    def _sale_multiplier(self, lot_id: str, object_id: int, obj: WorldObject) -> float:
        event = self._sale_events.get(lot_id)
        if not event:
            return 1.0
        kind = str(event.get("kind", ""))
        target = str(event.get("target", ""))
        discount = float(event.get("discount", 1.0))
        if kind == "all":
            return discount
        if kind == "type" and target == obj.type:
            return discount
        if kind == "rarity" and target == obj.rarity:
            return discount
        return 1.0

    def _adjust_demand(self, lot_id: str, object_id: int, up: bool) -> None:
        key = (lot_id, int(object_id))
        cur = float(self._price_multipliers.get(key, 1.0))
        delta = 0.03 if up else -0.02
        self._price_multipliers[key] = max(0.6, min(2.0, cur + delta))

    _RARITY_MULT = {
        "common": 1.0,
        "uncommon": 1.2,
        "rare": 1.5,
        "epic": 1.8,
        "legendary": 2.2,
    }

    def _effect_for_item(self, item: dict) -> dict:
        t = str(item.get("type", "Misc"))
        sub = str(item.get("sub_type", ""))
        m = self._RARITY_MULT.get(str(item.get("rarity", "common")), 1.0)

        if t == "Medical":
            return {
                "need": "comfort",
                "restore": round(35 * m, 1),
                "need2": "energy",
                "restore2": round(10 * m, 1),
                "emotion": "relief",
                "intensity": 0.5,
            }
        if t in {"Temporary", "Booster"}:
            return {
                "need": "fun",
                "restore": round(28 * m, 1),
                "need2": "energy",
                "restore2": round(8 * m, 1),
                "emotion": "joy",
                "intensity": 0.45,
            }
        if t == "Drug":
            return {
                "need": "fun",
                "restore": round(22 * m, 1),
                "neg_need": "energy",
                "neg_amount": 8.0,
                "emotion": "euphoria",
                "intensity": 0.55,
            }
        if t in {"Weapon", "Armor"} or sub in {
            "Primary",
            "Secondary",
            "Melee",
            "Pistol",
            "Rifle",
            "Clubbing",
            "Piercing",
            "Slashing",
        }:
            return {
                "need": "comfort",
                "restore": round(15 * m, 1),
                "career_bonus": 3.0,
                "emotion": "confidence",
                "intensity": 0.4,
            }
        if t == "Candy":
            return {
                "need": "hunger",
                "restore": round(15 * m, 1),
                "need2": "fun",
                "restore2": 8.0,
                "emotion": "joy",
                "intensity": 0.3,
            }
        if t == "Clothing":
            return {
                "need": "comfort",
                "restore": 8.0,
                "need2": "social",
                "restore2": round(10 * m, 1),
                "emotion": "confidence",
                "intensity": 0.35,
            }
        if t == "Jewelry":
            return {
                "need": "comfort",
                "restore": 5.0,
                "need2": "social",
                "restore2": round(12 * m, 1),
                "career_bonus": 2.0,
                "emotion": "pride",
                "intensity": 0.4,
            }
        if t == "Tool":
            return {
                "need": "fun",
                "restore": 10.0,
                "skill_xp": "handiness",
                "skill_amount": round(1.0 * m, 1),
                "emotion": "satisfaction",
                "intensity": 0.3,
            }
        if t == "Book":
            return {
                "need": "fun",
                "restore": round(18 * m, 1),
                "neg_need": "energy",
                "neg_amount": 5.0,
                "skill_xp": "logic",
                "skill_amount": round(1.5 * m, 1),
                "emotion": "interested",
                "intensity": 0.4,
            }
        if t == "Collectible":
            return {
                "need": "fun",
                "restore": round(15 * m, 1),
                "emotion": "joy",
                "intensity": 0.3,
            }
        if t == "Car":
            return {
                "need": "fun",
                "restore": 20.0,
                "need2": "comfort",
                "restore2": 10.0,
                "emotion": "excitement",
                "intensity": 0.4,
            }
        if t == "Flower":
            return {
                "need": "comfort",
                "restore": 10.0,
                "need2": "social",
                "restore2": 8.0,
                "emotion": "joy",
                "intensity": 0.35,
            }
        if t == "Alcohol":
            return {
                "need": "social",
                "restore": round(20 * m, 1),
                "need2": "fun",
                "restore2": 15.0,
                "neg_need": "bladder",
                "neg_amount": 10.0,
                "emotion": "euphoria",
                "intensity": 0.45,
            }
        if t == "Energy Drink":
            return {
                "need": "energy",
                "restore": round(28 * m, 1),
                "neg_need": "hunger",
                "neg_amount": 5.0,
                "emotion": "energized",
                "intensity": 0.4,
            }
        if t == "Enhancer":
            return {
                "need": "energy",
                "restore": round(12 * m, 1),
                "career_bonus": round(6 * m, 1),
                "emotion": "focused",
                "intensity": 0.4,
            }
        if t == "Artifact":
            return {
                "need": "fun",
                "restore": round(18 * m, 1),
                "skill_xp": "logic",
                "skill_amount": 1.0,
                "emotion": "curious",
                "intensity": 0.4,
            }
        if t == "Plushie":
            return {
                "need": "comfort",
                "restore": round(25 * m, 1),
                "social_drought_dec": 2,
                "emotion": "comfort",
                "intensity": 0.4,
            }
        if t == "Supply Pack":
            return {
                "need": "hunger",
                "restore": 15.0,
                "need2": "energy",
                "restore2": 10.0,
                "need3": "comfort",
                "restore3": 8.0,
                "emotion": "relief",
                "intensity": 0.4,
            }
        if t == "Furniture":
            return {
                "need": "comfort",
                "restore": round(25 * m, 1),
                "need2": "environment",
                "restore2": round(15 * m, 1),
                "emotion": "content",
                "intensity": 0.4,
            }
        if t == "Explosive":
            return {
                "need": "fun",
                "restore": 5.0,
                "neg_need": "comfort",
                "neg_amount": 10.0,
                "emotion": "fear",
                "intensity": 0.7,
            }
        if t == "Special":
            return {
                "need": "fun",
                "restore": round(20 * m, 1),
                "emotion": "excited",
                "intensity": 0.45,
            }
        # Material, Unused, Other
        return {
            "need": "fun",
            "restore": 10.0,
            "emotion": "satisfaction",
            "intensity": 0.25,
        }

    def _apply_item_effect(self, sim, effect: dict, item: dict) -> None:
        label = item.get("name", "item")

        def _set(need: str, delta: float) -> None:
            if hasattr(sim.needs, need):
                setattr(
                    sim.needs,
                    need,
                    max(0.0, min(100.0, float(getattr(sim.needs, need)) + delta)),
                )

        _set(str(effect.get("need", "fun")), float(effect.get("restore", 10.0)))
        if effect.get("need2"):
            _set(str(effect["need2"]), float(effect.get("restore2", 0.0)))
        if effect.get("need3"):
            _set(str(effect["need3"]), float(effect.get("restore3", 0.0)))
        if effect.get("neg_need"):
            _set(str(effect["neg_need"]), -float(effect.get("neg_amount", 0.0)))

        career_bonus = float(effect.get("career_bonus", 0.0))
        if career_bonus:
            sim.career_performance = min(100.0, sim.career_performance + career_bonus)

        skill_name = effect.get("skill_xp")
        if skill_name and hasattr(sim, "skills"):
            try:
                sim.skills.gain_xp(skill_name, float(effect.get("skill_amount", 1.0)))
            except Exception:
                pass

        social_dec = int(effect.get("social_drought_dec", 0))
        if social_dec:
            sim._social_drought_ticks = max(
                0, getattr(sim, "_social_drought_ticks", 0) - social_dec
            )

        sim.emotion.add(
            str(effect.get("emotion", "satisfaction")),
            float(effect.get("intensity", 0.25)),
            duration=4,
            source=f"use:{label}",
        )

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
            item["current_price"] = self.current_price(lot_id, int(object_id))
            out.append(item)
        out.sort(key=lambda x: (x.get("type", ""), x.get("name", "")))
        return out

    def market_state(self) -> dict:
        return {
            "sales": dict(self._sale_events),
            "hot_buy_types": sorted(
                self._buy_counts.items(), key=lambda x: x[1], reverse=True
            )[:5],
            "hot_use_types": sorted(
                self._use_counts.items(), key=lambda x: x[1], reverse=True
            )[:5],
        }

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
