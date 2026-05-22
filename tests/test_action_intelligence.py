import unittest

from core.action_intelligence import (
    apply_interruption,
    build_action_chain,
    compute_social_risk,
    score_action_feasibility,
)
from core.action_prereqs import prerequisites_met
from world.context_sensors import sense_context


class _Needs:
    def __init__(self, energy=60.0, social=60.0, hunger=60.0):
        self.energy = energy
        self.social = social
        self.hunger = hunger
        self.bladder = 50.0


class _Emotion:
    dominant = "neutral"


class _Skills:
    def __init__(self):
        self.levels = {"handiness": 3, "cleaning": 2, "cooking": 6, "charisma": 4}


class _Sim:
    def __init__(self):
        self.sim_id = "a"
        self.needs = _Needs()
        self.emotion = _Emotion()
        self.skills = _Skills()
        self.inventory_objects = [{"type": "Book"}]
        self._current_venue = {"noise": 0.2, "crowd": 0.3, "intimacy": 0.8}


class _Rel:
    friendship = 55.0
    romance = 25.0
    jealousy_score = 0.0


class _Engine:
    class _Clean:
        def room_state(self):
            return {"r1": {"cleanliness": 0.7}}

    class _Rels:
        def get(self, _a, _b):
            return _Rel()

    cleanliness = _Clean()
    relationships = _Rels()


class ActionIntelTests(unittest.TestCase):
    def test_feasibility_prefers_intimate_in_quiet_venue(self):
        sim = _Sim()
        env = {"ambient_noise": 0.1, "crowd_density": 0.2, "intimacy": 0.9}
        s = score_action_feasibility(sim, "discuss fears", env)
        self.assertGreater(s, 1.0)

    def test_social_risk_higher_for_flirt_when_low_romance(self):
        sim_a = _Sim()
        sim_b = _Sim()
        rel = _Rel()
        rel.romance = 5.0
        r = compute_social_risk(sim_a, sim_b, rel, "flirt")
        self.assertGreaterEqual(r, 0.35)

    def test_chain_builder_returns_progression(self):
        chain = build_action_chain(_Sim(), "flirt")
        self.assertEqual(chain[0], "chat")

    def test_interruption_router(self):
        out = apply_interruption({"fire_risk": 0.9})
        self.assertEqual(out, "respond to fire alarm")

    def test_prereqs_checked(self):
        sim = _Sim()
        rel = _Rel()
        self.assertTrue(prerequisites_met(sim, rel, "repair appliance"))

    def test_sensors_include_affordance(self):
        sim = _Sim()
        e = _Engine()
        sense = sense_context(e, sim, sim)
        self.assertIn("object_affordance_score", sense)


if __name__ == "__main__":
    unittest.main()
