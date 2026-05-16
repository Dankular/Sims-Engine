from config import SHOP_DEFS


def visit_shop(sim: "Sim", shop: dict) -> None:
    if sim.simoleons >= shop["cost"]:
        sim.simoleons -= shop["cost"]
        sim.needs.restore(shop["need"], shop["restore"])
        sim.emotion.add("joy", 0.4, duration=3, source=f"visited {shop['name']}")


__all__ = ["SHOP_DEFS", "visit_shop"]
