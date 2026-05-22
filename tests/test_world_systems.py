import unittest

from world.cleanliness import CleanlinessSystem
from world.programming import ProgrammingSystem
from world.cooking import CookingSystem
from world.wellness import WellnessSystem
from world.skill_classes import SkillClassSystem
from core.lifetime_aspirations import AspirationSystem
from world.life_state import LifeStateSystem
from world.neighborhoods import NeighborhoodSystem


class _DummyRel:
    def apply_deltas(self, _f, _r):
        return None


class _DummyRelationships:
    def get(self, _a, _b):
        return _DummyRel()


class _DummyLookup(dict):
    pass


class _DummyEmotion:
    dominant = "focus"
    dominant_valence = 0.6

    def add(self, *_args, **_kwargs):
        return None


class _DummyNeeds:
    def __init__(self):
        self.hunger = 40.0
        self.energy = 60.0
        self.fun = 55.0
        self.social = 50.0
        self.environment = 60.0


class _DummySkills:
    def __init__(self):
        self.levels = {
            "programming": 6.0,
            "cooking": 4.0,
            "gourmet_cooking": 2.0,
            "baking": 1.0,
            "cleaning": 2.0,
        }

    def gain_xp(self, _skill, _amount=0.0):
        return False


class _DummySim:
    def __init__(self, sim_id="s1"):
        self.sim_id = sim_id
        self.household_id = "h1"
        self.profile = {"traits": ["neat"], "outfit": "formalwear"}
        self.emotion = _DummyEmotion()
        self.needs = _DummyNeeds()
        self.skills = _DummySkills()
        self.autonomy_profile = {"harmony": 0.2}
        self.simoleons = 100.0
        self.reputation_score = 0.0
        self.hazard_flags = {"fire": 0.0}
        self.hacker_reputation = 0.0
        self.occult_type = "none"
        self.is_ghost = False
        self.occult_power = 0.0
        self.perks = set()


class _DummyEngine:
    def __init__(self):
        self.sims = [_DummySim()]
        self.relationships = _DummyRelationships()
        self.cleanliness = CleanlinessSystem()
        self._sim_lookup = _DummyLookup({self.sims[0].sim_id: self.sims[0]})
        self.tick_count = 1
        self.households = []


class WorldSystemsTests(unittest.TestCase):
    def test_cleanliness_tick_creates_room_state(self):
        eng = _DummyEngine()
        cs = CleanlinessSystem()
        cs.tick(eng)
        self.assertTrue(len(cs.room_state()) >= 1)

    def test_programming_tick_keeps_non_negative_money(self):
        eng = _DummyEngine()
        ps = ProgrammingSystem()
        for _ in range(10):
            ps.tick(eng)
        self.assertGreaterEqual(eng.sims[0].simoleons, 0.0)

    def test_cooking_tick_updates_quality_map(self):
        eng = _DummyEngine()
        ck = CookingSystem()
        ck.tick(eng)
        self.assertIn(eng.sims[0].sim_id, ck.last_meal_quality)

    def test_wellness_tick_updates_state(self):
        eng = _DummyEngine()
        ws = WellnessSystem()
        for _ in range(3):
            ws.tick(eng)
        state = ws.state_for(eng.sims[0].sim_id)
        self.assertIn("stress_level", state)
        self.assertIn("meditation_state", state)

    def test_skill_classes_system_generates_state(self):
        eng = _DummyEngine()
        sc = SkillClassSystem()
        for t in range(1, 6):
            eng.tick_count = t
            sc.tick(eng)
        self.assertTrue(len(sc.classes_state()) > 0)

    def test_lifetime_aspiration_system_bootstrap_and_progress(self):
        eng = _DummyEngine()
        sim = eng.sims[0]
        sim.profile["aspiration"] = "Knowledge"
        aps = AspirationSystem()
        aps.bootstrap(sim)
        aps.update_progress_from_wish(sim, 0.5)
        aps.tick(sim, eng, current_tick=5)
        asp = getattr(sim, "lifetime_aspiration")
        self.assertEqual(asp.id, "Knowledge")
        self.assertGreaterEqual(asp.progress, 0.5)
        self.assertIn(sim.sim_id, aps.legacy)

    def test_life_state_system_applies_state(self):
        eng = _DummyEngine()
        sim = eng.sims[0]
        sim.occult_type = "vampire"
        lss = LifeStateSystem()
        lss.tick(eng)
        state = lss.state_for(sim)
        self.assertEqual(state["life_state"], "vampire")
        self.assertIn("mind_control", state["abilities"])

    def test_neighborhood_system_world_state(self):
        eng = _DummyEngine()
        ns = NeighborhoodSystem()
        eng.tick_count = 10
        ns.tick(eng)
        state = ns.state_dict()
        self.assertIn("world", state)
        self.assertIn("districts", state["world"])


if __name__ == "__main__":
    unittest.main()
