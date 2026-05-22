import unittest

from world.pets import PetManager
from world.lot_layout import LotLayout
from world.objects import ObjectManager


class _DummyEmotion:
    def add(self, *_args, **_kwargs):
        return None


class _DummySim:
    def __init__(self):
        self.sim_id = "s1"
        self.household_id = "h1"
        self.pet_ids = []
        self.pet_records = {}
        self.simoleons = 500.0
        self.emotion = _DummyEmotion()


class _DummyEngine:
    def __init__(self, sim):
        self.sims = [sim]
        self.lot_layout = LotLayout()


class PetSystemTests(unittest.TestCase):
    def test_adopt_and_pet_actions(self):
        pm = PetManager()
        sim = _DummySim()
        adopted = pm.adopt_pet(sim)
        self.assertTrue(adopted.get("ok"))
        pet_id = adopted["pet"]["pet_id"]

        petted = pm.pet_pet(sim, pet_id)
        played = pm.play_with_pet(sim, pet_id)
        fed = pm.feed_pet(sim, pet_id)

        self.assertTrue(petted.get("ok"))
        self.assertTrue(played.get("ok"))
        self.assertTrue(fed.get("ok"))
        self.assertIn("mood", fed["pet"])

    def test_bowl_refill_and_auto_feed(self):
        pm = PetManager()
        sim = _DummySim()
        lot = LotLayout()
        # Place explicit bowl object in home kitchen
        lot.place(
            sim.household_id,
            "kitchen",
            {
                "id": 900001,
                "name": "Dog Feeder Bowl",
                "type": "Pet Supply",
                "sub_type": "pet_bowl",
                "market_price": 25,
                "tradable": False,
                "rarity": "common",
                "weight": 1.0,
                "slot": "utility",
                "details": {},
            },
        )
        adopted = pm.adopt_pet(sim)
        self.assertTrue(adopted.get("ok"))
        pet_id = adopted["pet"]["pet_id"]
        # Lower hunger first
        sim.pet_records[pet_id].hunger = 10.0
        refill = pm.refill_food_bowl(sim, lot, sim.household_id)
        self.assertTrue(refill.get("ok"))

        eng = _DummyEngine(sim)
        eng.lot_layout = lot
        pm.tick(eng)
        self.assertGreater(sim.pet_records[pet_id].hunger, 10.0)

    def test_neglect_can_remove_pet_and_recovery_boosts_bond(self):
        pm = PetManager()
        sim = _DummySim()
        adopted = pm.adopt_pet(sim)
        self.assertTrue(adopted.get("ok"))
        pet_id = adopted["pet"]["pet_id"]
        pet = sim.pet_records[pet_id]
        sim.disable_pet_autocare = True
        pet.hunger = 1.0
        pet.fun = 1.0
        pet.cleanliness = 1.0
        lot = LotLayout()
        eng = _DummyEngine(sim)
        eng.lot_layout = lot
        for _ in range(30):
            pm.tick(eng)
            if pet_id not in sim.pet_records:
                break
        self.assertNotIn(pet_id, sim.pet_records)

        adopted2 = pm.adopt_pet(sim)
        self.assertTrue(adopted2.get("ok"))
        pet2_id = adopted2["pet"]["pet_id"]
        pet2 = sim.pet_records[pet2_id]
        pet2.hunger = 95.0
        pet2.fun = 95.0
        pet2.cleanliness = 95.0
        base_bond = pet2.bond
        for _ in range(8):
            pm.tick(eng)
        self.assertGreaterEqual(pet2.bond, base_bond)

    def test_pet_store_strict_focus_stock(self):
        mgr = ObjectManager()
        mgr.assign_world_objects(
            ["shop_petstore"],
            density=24,
            lot_rules={
                "shop_petstore": {
                    "type": "business",
                    "venue_assignment": "retail_store",
                    "focus_types": ["Collectible", "Plushie", "Special"],
                    "strict_focus": True,
                }
            },
        )
        stock = mgr.lot_stock_state("shop_petstore")
        self.assertTrue(len(stock) > 0)
        allowed = {"collectible", "plushie", "special"}
        types = {str(i.get("type", "")).lower() for i in stock}
        # strict focus allows tiny tail, but dominant should be allowed
        allowed_count = sum(
            1 for i in stock if str(i.get("type", "")).lower() in allowed
        )
        self.assertGreaterEqual(allowed_count, max(1, int(len(stock) * 0.7)))


if __name__ == "__main__":
    unittest.main()
