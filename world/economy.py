from __future__ import annotations
from typing import TYPE_CHECKING

from config import SHOP_DEFS

if TYPE_CHECKING:
    from core.sim import Sim


def visit_shop(sim: "Sim", shop: dict, engine=None) -> None:
    cost = shop["cost"]
    if sim.simoleons < cost:
        return
    if engine is not None and hasattr(engine, "_charge"):
        ok = engine._charge(
            sim, cost, "shop_purchase",
            shop_name=shop.get("name", ""), item=shop.get("need", ""),
        )
        if not ok:
            return
    else:
        sim.simoleons -= cost
    sim.needs.restore(shop["need"], shop["restore"])
    sim.emotion.add("joy", 0.4, duration=3, source=f"visited {shop['name']}")


__all__ = ["SHOP_DEFS", "visit_shop"]
