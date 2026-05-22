import unittest

from datasets.open_world_actions import (
    allow_action_for_state,
    load_open_world_action_index,
    normalize_venue_name,
)
from validate_galaxea_actions import canonicalize_task_name


class _Needs:
    def __init__(self, energy=50.0, social=50.0, hunger=50.0):
        self.energy = energy
        self.social = social
        self.hunger = hunger


class _Sim:
    def __init__(self, energy=50.0, social=50.0, hunger=50.0):
        self.needs = _Needs(energy=energy, social=social, hunger=hunger)


class _Rel:
    def __init__(self, friendship=0.0, romance=0.0):
        self.friendship = friendship
        self.romance = romance


class OpenWorldActionTests(unittest.TestCase):
    def test_canonicalize_fixes_noise_tokens(self):
        raw = "Storage_Tools_20250722_008.tar.gz"
        canon = canonicalize_task_name(raw)
        self.assertEqual(canon, "Storage_Tools")

        noisy = "Turn_0n_The_Food_Pot20250618_001.tar.gz"
        canon_noisy = canonicalize_task_name(noisy)
        self.assertTrue(canon_noisy.startswith("Turn_On_The_Food_Pot"))

    def test_normalize_venue_name_maps_core_venues(self):
        self.assertEqual(normalize_venue_name("home (1:1)"), "home")
        self.assertEqual(normalize_venue_name("shopping center"), "retail_store")
        self.assertEqual(normalize_venue_name("office"), "office")

    def test_state_filter_blocks_energy_heavy_actions_when_exhausted(self):
        sim = _Sim(energy=12.0, social=70.0, hunger=70.0)
        rel = _Rel(friendship=40.0, romance=0.0)
        clean_action = {"action_text": "clean the sink", "intent": "clean"}
        self.assertFalse(allow_action_for_state(clean_action, sim, rel))

    def test_state_filter_blocks_stranger_social_when_lonely(self):
        sim = _Sim(energy=70.0, social=10.0, hunger=70.0)
        rel = _Rel(friendship=5.0, romance=0.0)
        social_action = {"action_text": "ring the landline", "intent": "social"}
        self.assertFalse(allow_action_for_state(social_action, sim, rel))

    def test_action_index_loads_from_generated_catalog(self):
        idx = load_open_world_action_index()
        self.assertIn("actions", idx)
        self.assertIn("by_intent", idx)
        self.assertIsInstance(idx["actions"], list)


if __name__ == "__main__":
    unittest.main()
