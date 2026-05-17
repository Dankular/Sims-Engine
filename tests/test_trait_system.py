import unittest

from core.traits import (
    apply_trait_conflicts,
    derive_autonomy_profile,
    interaction_weight_modifier,
    skill_gain_multiplier,
    trait_compatibility_bonus,
)


class _DummySim:
    def __init__(self, traits=None, reward=None, temporary=None, death=None):
        self.profile = {
            "traits": traits or [],
            "ocean": {
                "openness": 0.5,
                "conscientiousness": 0.5,
                "extraversion": 0.5,
                "agreeableness": 0.5,
                "neuroticism": 0.5,
            },
        }
        self.reward_traits = set(reward or [])
        self.temporary_traits = set(temporary or [])
        self.death_traits = set(death or [])
        self.autonomy_profile = derive_autonomy_profile(self)


class TraitSystemTests(unittest.TestCase):
    def test_conflict_resolution_removes_opposite_trait(self):
        resolved = apply_trait_conflicts({"active", "lazy"})
        self.assertEqual(len(resolved), 1)

    def test_autonomy_profile_accumulates_weights(self):
        sim = _DummySim(traits=["romantic", "good"])
        self.assertGreater(sim.autonomy_profile.get("romance", 0.0), 0.0)
        self.assertGreater(sim.autonomy_profile.get("harmony", 0.0), 0.0)

    def test_interaction_weight_modifier_changes_by_trait(self):
        romantic = _DummySim(traits=["romantic"])
        unflirty = _DummySim(traits=["unflirty"])
        r_mod = interaction_weight_modifier(romantic, "flirt")
        u_mod = interaction_weight_modifier(unflirty, "flirt")
        self.assertGreater(r_mod, u_mod)

    def test_skill_gain_multiplier_boosts_configured_skills(self):
        sim = _DummySim(traits=["bookworm"])
        self.assertGreater(skill_gain_multiplier(sim, "logic"), 1.0)

    def test_trait_compatibility_bonus_balances_positive_and_negative(self):
        a = _DummySim(traits=["romantic", "good"])
        b = _DummySim(traits=["romantic", "mean"])
        bonus = trait_compatibility_bonus(a, b)
        self.assertGreaterEqual(bonus, -0.25)
        self.assertLessEqual(bonus, 0.25)


if __name__ == "__main__":
    unittest.main()
