from __future__ import annotations

from dataclasses import dataclass
import json
import random
import re
from typing import Any, Callable
from urllib.request import urlopen

from world.weather import WEATHER_STATES


NativeHandler = Callable[..., Any]

NATIVE_DB_URL = (
    "https://raw.githubusercontent.com/alloc8or/gta5-nativedb-data/master/natives.json"
)
NATIVE_DB_TIMEOUT_SECONDS = 2

NAMESPACE_MAP = {
    "MISC": "WORLD",
    "PATH": "WORLD",
    "PED": "SIM",
    "PLAYER": "SIM",
    "CAM": "SCENE",
    "CAMERA": "SCENE",
    "GRAPHICS": "SCENE",
    "TASK": "SCENE",
    "MONEY": "SHOPS",
    "STATS": "SHOPS",
    "OBJECT": "OBJECT",
    "ENTITY": "OBJECT",
}


@dataclass(frozen=True)
class NativeSpec:
    name: str
    namespace: str
    description: str
    handler: NativeHandler
    source: str = "engine"


class NativeRegistry:
    def __init__(self, engine) -> None:
        self.engine = engine
        self._specs: dict[str, NativeSpec] = {}
        self._register_core_natives()
        self._bootstrap_dynamic_catalog()

    def register(
        self,
        namespace: str,
        name: str,
        description: str,
        source: str = "engine",
    ) -> Callable[[NativeHandler], NativeHandler]:
        native_name = name.strip().upper()
        native_namespace = namespace.strip().upper()

        def _decorator(handler: NativeHandler) -> NativeHandler:
            self._specs[native_name] = NativeSpec(
                name=native_name,
                namespace=native_namespace,
                description=description,
                handler=handler,
                source=source,
            )
            return handler

        return _decorator

    def call(self, name: str, **kwargs) -> Any:
        native_name = str(name or "").strip().upper()
        spec = self._specs.get(native_name)
        if spec is None:
            return {"ok": False, "reason": "unknown_native", "native": native_name}
        try:
            result = spec.handler(**kwargs)
        except TypeError as exc:
            return {
                "ok": False,
                "reason": "bad_arguments",
                "native": native_name,
                "error": str(exc),
            }
        except Exception as exc:
            return {
                "ok": False,
                "reason": "native_error",
                "native": native_name,
                "error": str(exc),
            }
        if isinstance(result, dict) and "ok" not in result:
            return {"ok": True, **result}
        return result

    def list(self, namespace: str | None = None) -> list[dict[str, str]]:
        ns = str(namespace or "").strip().upper()
        out: list[dict[str, str]] = []
        for spec in sorted(self._specs.values(), key=lambda x: (x.namespace, x.name)):
            if ns and spec.namespace != ns:
                continue
            out.append(
                {
                    "name": spec.name,
                    "namespace": spec.namespace,
                    "description": spec.description,
                    "source": spec.source,
                }
            )
        return out

    def _bootstrap_dynamic_catalog(self) -> None:
        try:
            with urlopen(NATIVE_DB_URL, timeout=NATIVE_DB_TIMEOUT_SECONDS) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return

        if not isinstance(payload, dict):
            return

        for gta_namespace, entries in payload.items():
            mapped = NAMESPACE_MAP.get(str(gta_namespace).upper())
            if mapped is None or not isinstance(entries, dict):
                continue
            for native_info in entries.values():
                if not isinstance(native_info, dict):
                    continue
                native_name = str(native_info.get("name", "")).strip().upper()
                if not native_name or native_name in self._specs:
                    continue
                desc = f"Auto-mapped from {gta_namespace}::{native_name}"
                self._specs[native_name] = NativeSpec(
                    name=native_name,
                    namespace=mapped,
                    description=desc,
                    handler=self._make_dynamic_handler(native_name),
                    source="native_db",
                )

    def _make_dynamic_handler(self, native_name: str) -> NativeHandler:
        def _dynamic(**kwargs):
            return self._dispatch_dynamic(native_name, kwargs)

        return _dynamic

    def _dispatch_dynamic(self, native_name: str, kwargs: dict[str, Any]) -> dict:
        name = native_name.upper()

        if "WEATHER" in name:
            if name.startswith("SET_"):
                state = (
                    kwargs.get("state")
                    or kwargs.get("weather")
                    or kwargs.get("weather_type")
                )
                if state is None:
                    return {
                        "ok": False,
                        "reason": "missing_argument",
                        "required": "state",
                    }
                return self.call("SET_WEATHER_STATE", state=str(state))
            return self.call("GET_WEATHER_STATE")

        if ("TIME" in name and name.startswith("GET_")) or name in {
            "GET_GAME_TIMER",
            "GET_REAL_WORLD_TIME",
        }:
            return self.call("GET_TICK_COUNT")

        if re.search(r"(BUY|PURCHASE).*(ITEM|OBJECT)", name):
            return self.call(
                "BUY_ITEM",
                sim_id=kwargs.get("sim_id"),
                lot_id=kwargs.get("lot_id", self.engine.shopping.lot_id),
                object_id=kwargs.get("object_id"),
                qty=kwargs.get("qty", 1),
            )

        if re.search(r"SELL.*(ITEM|OBJECT)", name):
            return self.call(
                "SELL_ITEM",
                sim_id=kwargs.get("sim_id"),
                object_id=kwargs.get("object_id"),
                qty=kwargs.get("qty", 1),
            )

        if re.search(r"GIFT.*(ITEM|OBJECT)", name):
            return self.call(
                "GIFT_ITEM",
                giver_id=kwargs.get("giver_id") or kwargs.get("sim_id"),
                receiver_id=kwargs.get("receiver_id") or kwargs.get("target_sim_id"),
                object_id=kwargs.get("object_id"),
            )

        if re.search(r"SET_.*(SIM|PED).*(NEED|ENERGY|HUNGER|FUN|SOCIAL|HYGIENE)", name):
            need = (
                kwargs.get("need")
                or kwargs.get("need_name")
                or kwargs.get("stat")
                or "energy"
            )
            value = kwargs.get("value", kwargs.get("amount", 50.0))
            return self.call(
                "SET_SIM_NEED",
                sim_id=kwargs.get("sim_id"),
                need=str(need),
                value=float(value),
            )

        if re.search(r"(ADD|SET).*(MONEY|CASH|SIMOLEON)", name):
            if name.startswith("SET_"):
                sim = self.engine._sim_lookup.get(str(kwargs.get("sim_id", "")))
                if sim is None:
                    return {"ok": False, "reason": "sim_not_found"}
                current = float(getattr(sim, "simoleons", 0.0))
                target = float(kwargs.get("amount", kwargs.get("value", current)))
                return self.call(
                    "ADD_SIMOLEONS", sim_id=sim.sim_id, amount=target - current
                )
            return self.call(
                "ADD_SIMOLEONS",
                sim_id=kwargs.get("sim_id"),
                amount=kwargs.get("amount", kwargs.get("value", 0.0)),
            )

        if re.search(r"(SET|CREATE|PLACE).*OBJECT", name):
            return self.call(
                "PLACE_OBJECT",
                sim_id=kwargs.get("sim_id"),
                lot_id=kwargs.get("lot_id"),
                zone=kwargs.get("zone", "living_room"),
                object_id=kwargs.get("object_id"),
            )

        if re.search(r"(DELETE|REMOVE|DETACH).*OBJECT", name):
            return self.call(
                "REMOVE_OBJECT",
                sim_id=kwargs.get("sim_id"),
                lot_id=kwargs.get("lot_id"),
                object_id=kwargs.get("object_id"),
            )

        if "VENUE" in name:
            if name.startswith("SET_"):
                return self.call("SET_ACTIVE_VENUE", name=kwargs.get("name", ""))
            if name.startswith("ROTATE_") or "NEXT" in name:
                return self.call("ROTATE_ACTIVE_VENUE")

        return {
            "ok": False,
            "reason": "native_not_yet_bound",
            "native": native_name,
            "message": "Native exists in catalog but needs an engine binding.",
        }

    def _register_core_natives(self) -> None:
        @self.register("WORLD", "GET_TICK_COUNT", "Return current world tick")
        def _get_tick_count() -> dict:
            return {"tick": self.engine.tick_count}

        @self.register("WORLD", "GET_WEATHER_STATE", "Return current weather state")
        def _get_weather_state() -> dict:
            return {"weather": self.engine.weather.state_dict()}

        @self.register("WORLD", "SET_WEATHER_STATE", "Force weather by state name")
        def _set_weather_state(state: str) -> dict:
            key = str(state or "").strip().lower()
            if key not in WEATHER_STATES:
                return {"ok": False, "reason": "unknown_weather_state", "state": key}
            self.engine.weather.current = WEATHER_STATES[key]
            self.engine._bus.emit(
                "weather_changed",
                weather=self.engine.weather.current.name,
                temperature=self.engine.weather.current.temperature,
                tick=self.engine.tick_count,
            )
            return {"weather": self.engine.weather.state_dict()}

        @self.register("SCENE", "SET_ACTIVE_VENUE", "Switch active simulation venue")
        def _set_active_venue(name: str) -> dict:
            target = str(name or "").strip().lower()
            for venue in self.engine.venues_catalog:
                if str(venue.get("name", "")).strip().lower() == target:
                    self.engine._venue = dict(venue)
                    return {"venue": dict(self.engine._venue)}
            return {"ok": False, "reason": "unknown_venue", "name": name}

        @self.register(
            "SCENE", "ROTATE_ACTIVE_VENUE", "Rotate to a random active venue"
        )
        def _rotate_active_venue() -> dict:
            self.engine._venue = {
                **random.choice(self.engine.venues_catalog),
                **self.engine._audio_sensor.sense(),
            }
            return {"venue": dict(self.engine._venue)}

        @self.register("SIM", "SET_SIM_NEED", "Set a sim need value 0-100")
        def _set_sim_need(sim_id: str, need: str, value: float) -> dict:
            sim = self.engine._sim_lookup.get(str(sim_id or ""))
            if sim is None:
                return {"ok": False, "reason": "sim_not_found", "sim_id": sim_id}
            need_name = str(need or "").strip().lower()
            if not hasattr(sim.needs, need_name):
                return {"ok": False, "reason": "unknown_need", "need": need_name}
            setattr(sim.needs, need_name, max(0.0, min(100.0, float(value))))
            return {
                "sim_id": sim.sim_id,
                "need": need_name,
                "value": getattr(sim.needs, need_name),
            }

        @self.register("SIM", "ADD_SIMOLEONS", "Add or remove simoleons from a sim")
        def _add_simoleons(sim_id: str, amount: float) -> dict:
            sim = self.engine._sim_lookup.get(str(sim_id or ""))
            if sim is None:
                return {"ok": False, "reason": "sim_not_found", "sim_id": sim_id}
            sim.simoleons = round(max(0.0, float(sim.simoleons) + float(amount)), 2)
            return {"sim_id": sim.sim_id, "simoleons": sim.simoleons}

        @self.register("SIM", "SET_SIM_EMOTION", "Push an emotion onto a sim")
        def _set_sim_emotion(
            sim_id: str, emotion: str, intensity: float = 0.4, duration: int = 3
        ) -> dict:
            sim = self.engine._sim_lookup.get(str(sim_id or ""))
            if sim is None:
                return {"ok": False, "reason": "sim_not_found", "sim_id": sim_id}
            tag = str(emotion or "").strip().lower() or "neutral"
            sim.emotion.add(
                tag, float(intensity), duration=max(1, int(duration)), source="native"
            )
            return {
                "sim_id": sim.sim_id,
                "emotion": tag,
                "intensity": float(intensity),
                "duration": int(duration),
            }

        @self.register("SHOPS", "BUY_ITEM", "Buy item from lot stock")
        def _buy_item(sim_id: str, lot_id: str, object_id: int, qty: int = 1) -> dict:
            return self.engine.buy_item(
                str(sim_id), str(lot_id), int(object_id), int(qty)
            )

        @self.register("SHOPS", "SELL_ITEM", "Sell item from a sim inventory")
        def _sell_item(sim_id: str, object_id: int, qty: int = 1) -> dict:
            return self.engine.sell_item(str(sim_id), int(object_id), int(qty))

        @self.register("SHOPS", "GIFT_ITEM", "Gift item between sims")
        def _gift_item(
            giver_id: str, receiver_id: str, object_id: int | None = None
        ) -> dict:
            return self.engine.gift_item(str(giver_id), str(receiver_id), object_id)

        @self.register("OBJECT", "GET_LOT_STOCK", "Get stock and prices for a lot")
        def _get_lot_stock(lot_id: str) -> dict:
            return {
                "lot_id": str(lot_id),
                "stock": self.engine.objects.lot_stock_state(str(lot_id)),
            }

        @self.register(
            "OBJECT", "PLACE_OBJECT", "Place inventory object into lot layout"
        )
        def _place_object(sim_id: str, lot_id: str, zone: str, object_id: int) -> dict:
            sim = self.engine._sim_lookup.get(str(sim_id or ""))
            if sim is None:
                return {"ok": False, "reason": "sim_not_found", "sim_id": sim_id}
            inv = list(getattr(sim, "inventory_objects", []))
            selected: dict[str, Any] | None = None
            kept: list[dict] = []
            consumed = False
            for item in inv:
                if not consumed and int(item.get("id", -1)) == int(object_id):
                    selected = dict(item)
                    consumed = True
                    continue
                kept.append(item)
            if selected is None:
                return {"ok": False, "reason": "item_not_found"}
            result = self.engine.lot_layout.place(str(lot_id), str(zone), selected)
            if not result.get("ok"):
                return result
            sim.inventory_objects = kept
            sim.inventory = [o["name"] for o in sim.inventory_objects]
            return {
                "sim_id": sim.sim_id,
                "lot_id": str(lot_id),
                "zone": str(zone),
                "object": selected,
            }

        @self.register(
            "OBJECT", "REMOVE_OBJECT", "Remove placed object and return to sim"
        )
        def _remove_object(sim_id: str, lot_id: str, object_id: int) -> dict:
            sim = self.engine._sim_lookup.get(str(sim_id or ""))
            if sim is None:
                return {"ok": False, "reason": "sim_not_found", "sim_id": sim_id}
            removed = self.engine.lot_layout.remove(str(lot_id), int(object_id))
            if not removed.get("ok"):
                return removed
            inv = list(getattr(sim, "inventory_objects", []))
            inv.append(dict(removed.get("item", {})))
            constrained = self.engine.objects._apply_inventory_constraints(sim, inv)
            if len(constrained) < len(inv):
                return {"ok": False, "reason": "inventory_full"}
            sim.inventory_objects = constrained
            sim.inventory = [o["name"] for o in sim.inventory_objects]
            return {"sim_id": sim.sim_id, "lot_id": str(lot_id), "removed": removed}
