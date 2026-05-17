import unittest

from core.genetics import express_eye_color, pick_gene_pair
from core.social_chemistry import chemistry_level, attraction
from world.temperature_model import zone_from_temp


class MechanicsTests(unittest.TestCase):
    def test_chemistry_level_bands(self):
        self.assertEqual(chemistry_level(-30), "repulsion")
        self.assertEqual(chemistry_level(10), "low")
        self.assertEqual(chemistry_level(50), "medium")

    def test_attraction_returns_bounded_score(self):
        a = {
            "aspiration": "Romance",
            "zodiac": "Aries",
            "traits": ["romantic"],
            "attraction_profile": {
                "turn_ons": ["romantic"],
                "turn_off": "mean",
                "personality": {
                    "neat": 7,
                    "outgoing": 7,
                    "active": 6,
                    "playful": 8,
                    "nice": 8,
                },
            },
        }
        b = {
            "aspiration": "Romance",
            "zodiac": "Leo",
            "traits": ["romantic"],
            "attraction_profile": {
                "turn_ons": ["romantic"],
                "turn_off": "evil",
                "personality": {
                    "neat": 6,
                    "outgoing": 8,
                    "active": 5,
                    "playful": 7,
                    "nice": 7,
                },
            },
        }
        score = attraction(a, b)
        self.assertGreaterEqual(score, -100)
        self.assertLessEqual(score, 100)

    def test_genetics_gene_pair(self):
        pair = pick_gene_pair(("brown", "green"), ("light_blue", "dark_blue"))
        self.assertEqual(len(pair), 2)
        self.assertIn(
            express_eye_color(pair),
            {"brown", "green", "light_blue", "dark_blue", "alien", "gray"},
        )

    def test_temperature_zones(self):
        self.assertEqual(zone_from_temp(-80), "very_cold")
        self.assertEqual(zone_from_temp(0), "comfortable")
        self.assertEqual(zone_from_temp(95), "very_hot")


if __name__ == "__main__":
    unittest.main()
