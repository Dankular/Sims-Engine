import unittest

from core.adaptive_policy import AdaptiveBandit


class _DummyEmotion:
    dominant_valence = 0.6


class _DummySim:
    def __init__(self, sim_id: str):
        self.sim_id = sim_id
        self.emotion = _DummyEmotion()
        self.reputation_score = 0.0


class AdaptivePolicyTests(unittest.TestCase):
    def test_bandit_score_and_observe(self):
        b = AdaptiveBandit(store_path="datasets/.sim_cache/test_bandit.json")
        b.enabled = True
        a = _DummySim("a")
        c = _DummySim("c")
        s1 = b.score(a, c, "chat", 1.0)
        self.assertGreater(s1, 0.0)
        for _ in range(5):
            b.observe("a", "chat", reward=1.2)
        s2 = b.score(a, c, "chat", 1.0)
        self.assertGreater(s2, 0.0)
        dbg = b.debug_for("a", limit=2)
        self.assertTrue(len(dbg) >= 1)


if __name__ == "__main__":
    unittest.main()
