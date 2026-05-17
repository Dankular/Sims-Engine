import json
import tempfile
import unittest
from pathlib import Path

from world.objects import ObjectManager


class _Sim:
    def __init__(self):
        self.simoleons = 10000.0
        self.inventory = []
        self.inventory_objects = []
        self.inventory_max_slots = 3
        self.inventory_max_weight = 5.0
        self.inventory_slot_limits = {"hand": 1, "body": 1, "utility": 2}


class ObjectManagerTests(unittest.TestCase):
    def setUp(self):
        self._tds = []

    def tearDown(self):
        for td in self._tds:
            td.cleanup()

    def _manager(self) -> ObjectManager:
        payload = {
            "items": [
                {
                    "id": 1,
                    "name": "Knife",
                    "type": "Weapon",
                    "sub_type": "Melee",
                    "is_tradable": True,
                    "value": {"market_price": 500},
                },
                {
                    "id": 2,
                    "name": "Medkit",
                    "type": "Medical",
                    "sub_type": "Medical",
                    "is_tradable": True,
                    "value": {"market_price": 1200},
                },
                {
                    "id": 3,
                    "name": "Armor Vest",
                    "type": "Armor",
                    "sub_type": "Armor",
                    "is_tradable": True,
                    "value": {"market_price": 8000},
                },
            ]
        }
        td = tempfile.TemporaryDirectory()
        self._tds.append(td)
        p = Path(td.name) / "items.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        mgr = ObjectManager(catalog_path=str(p))
        return mgr

    def test_assign_inventory_respects_constraints(self):
        mgr = self._manager()
        sim = _Sim()
        mgr.assign_sim_inventory(sim, count=3)
        self.assertLessEqual(len(sim.inventory_objects), sim.inventory_max_slots)
        self.assertLessEqual(mgr.inventory_weight(sim), sim.inventory_max_weight)

    def test_buy_and_sell_flow(self):
        mgr = self._manager()
        sim = _Sim()
        lot_id = "lot_a"
        mgr.lot_object_stock[lot_id] = {1: 2}
        ok_buy = mgr.buy_object(sim, lot_id, 1, qty=1)
        self.assertTrue(ok_buy)
        self.assertEqual(mgr.lot_object_stock[lot_id][1], 1)
        before_cash = sim.simoleons
        ok_sell = mgr.sell_object(sim, 1, qty=1)
        self.assertTrue(ok_sell)
        self.assertGreater(sim.simoleons, before_cash)

    def test_world_object_stock_created(self):
        mgr = self._manager()
        mgr.assign_world_objects(
            ["lot1"],
            density=3,
            lot_rules={"lot1": {"type": "business", "venue_assignment": "gym"}},
        )
        self.assertIn("lot1", mgr.lot_object_stock)
        self.assertTrue(sum(mgr.lot_object_stock["lot1"].values()) >= 1)


if __name__ == "__main__":
    unittest.main()
