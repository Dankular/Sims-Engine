from __future__ import annotations

import random
from config import MARKET_SHOPS


class ShoppingCenter:
    def __init__(self, lot_id: str = "shopping_center") -> None:
        self.lot_id = lot_id
        self.shop_lots = [
            str(s.get("lot_id", "")) for s in MARKET_SHOPS if s.get("lot_id")
        ]

    def tick(self, engine) -> None:
        sims = [s for s in engine.sims if getattr(s, "lod_tier", None) is not None]
        if not sims:
            return
        for sim in sims:
            self._maybe_buy(engine, sim)
            self._maybe_sell(engine, sim)

        if len(sims) >= 2 and random.random() < 0.12:
            giver = random.choice(sims)
            receiver = random.choice([s for s in sims if s.sim_id != giver.sim_id])
            self._maybe_gift(engine, giver, receiver)

    def _maybe_buy(self, engine, sim) -> None:
        budget = float(getattr(sim, "simoleons", 0.0))
        if budget < 120:
            return
        if random.random() > 0.18:
            return

        target_lot = self._pick_shop_lot_for_need(sim)
        stock = engine.objects.lot_object_stock.get(target_lot, {})
        if not stock:
            return

        candidates = [
            (oid, qty)
            for oid, qty in stock.items()
            if qty > 0
            and engine.objects.catalog.get(int(oid)) is not None
            and float(engine.objects.current_price(target_lot, int(oid)))
            <= budget * 0.35
        ]
        if not candidates:
            return

        object_id, _qty = random.choice(candidates)
        engine.buy_item(sim.sim_id, target_lot, int(object_id), qty=1)

    def _pick_shop_lot_for_need(self, sim) -> str:
        pressure = sim.needs.pressure_vector()
        need = max(pressure, key=pressure.get)
        if need == "hunger":
            return "shop_grocer"
        if need in {"fun", "social"}:
            return random.choice(["shop_bookstore", "shop_arcade"])
        if need in {"energy", "comfort", "hygiene"}:
            return "shop_outfitter"
        if self.shop_lots:
            return random.choice(self.shop_lots)
        return self.lot_id

    def _maybe_sell(self, engine, sim) -> None:
        inv = list(getattr(sim, "inventory_objects", []))
        if len(inv) < 3:
            return
        if random.random() > 0.12:
            return

        sellable = [i for i in inv if i.get("tradable", True)]
        if not sellable:
            return
        chosen = random.choice(sellable)
        object_id = int(chosen.get("id", -1))
        if object_id < 0:
            return
        engine.sell_item(sim.sim_id, object_id, qty=1)

    def _maybe_gift(self, engine, giver, receiver) -> None:
        if random.random() > 0.28:
            return
        engine.gift_item(giver.sim_id, receiver.sim_id)
