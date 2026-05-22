import os
import tempfile
import unittest

from core.sim import Sim
from core.sentiments import SentimentRecord
from engine.engine import SimEngine
from identity.profile_factory import generate_sim_profile
from llm.mock_backend import MockLLMBackend
from persistence.sqlite import PersistenceLayer
from world.households import assign_households


class PersistenceRestartTests(unittest.TestCase):
    def test_restart_restores_inventory_age_relationships_and_memories(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "restart_state.db")

            profile_a = generate_sim_profile()
            profile_b = generate_sim_profile()
            sim_a = Sim(profile_a)
            sim_b = Sim(profile_b)

            sim_a.profile["age"] = 41
            sim_b.profile["age"] = 36

            db = PersistenceLayer(db_path=db_path)
            engine = SimEngine(
                sims=[sim_a, sim_b], llm=MockLLMBackend(), datasets=None, db=db
            )
            engine.households = assign_households(engine.sims)
            sim_a.inventory = ["snack", "book", "rare_seed"]
            sim_a.inventory_objects = [
                {
                    "obj_id": "obj_seed_01",
                    "name": "Rare Seed",
                    "type": "collectible",
                    "quality": 4,
                    "weight": 0.2,
                    "slot": "utility",
                }
            ]

            rel = engine.relationships.get(sim_a.sim_id, sim_b.sim_id)
            rel.friendship = 62.5
            rel.romance = 24.0
            rel.interactions = 11
            rel.memories = [{"id": "m1", "tag": "Shared a joke", "valence": 1.1}]
            rel.sentiments = [
                SentimentRecord(
                    name="first_kiss",
                    added_tick=7,
                    expires_tick=-1,
                    source="kiss on couch",
                )
            ]

            engine.memory_store.write(
                sim_a.sim_id,
                sim_b.sim_id,
                "Had a heartfelt conversation",
                0.95,
                interaction_id="abc123",
                tick=8,
            )
            engine._tick_count = 9
            db.save_state(engine)
            db.close()

            db2 = PersistenceLayer(db_path=db_path)
            try:
                snapshot = db2.load_state()
                self.assertIsNotNone(snapshot)

                restored_sims = [
                    Sim(sim_state["profile"])
                    for sim_state in snapshot["sims"]
                    if isinstance(sim_state, dict)
                    and isinstance(sim_state.get("profile"), dict)
                ]
                restored_engine = SimEngine(
                    sims=restored_sims,
                    llm=MockLLMBackend(),
                    datasets=None,
                    db=db2,
                )
                restored_engine.households = assign_households(restored_engine.sims)
                db2.restore_engine(restored_engine, snapshot)

                restored_a = next(
                    s for s in restored_engine.sims if s.sim_id == sim_a.sim_id
                )
                restored_b = next(
                    s for s in restored_engine.sims if s.sim_id == sim_b.sim_id
                )

                self.assertEqual(restored_a.profile.get("age"), 41)
                self.assertEqual(restored_b.profile.get("age"), 36)
                self.assertEqual(restored_a.inventory, ["snack", "book", "rare_seed"])
                self.assertEqual(len(restored_a.inventory_objects), 1)
                self.assertEqual(
                    restored_a.inventory_objects[0].get("obj_id"), "obj_seed_01"
                )

                restored_rel = restored_engine.relationships.get(
                    restored_a.sim_id, restored_b.sim_id
                )
                self.assertAlmostEqual(restored_rel.friendship, 62.5, places=2)
                self.assertAlmostEqual(restored_rel.romance, 24.0, places=2)
                self.assertEqual(restored_rel.interactions, 11)
                self.assertEqual(restored_rel.memories[0]["tag"], "Shared a joke")
                self.assertEqual(len(restored_rel.sentiments), 1)
                self.assertEqual(restored_rel.sentiments[0].name, "first_kiss")

                pair_key = f"{min(sim_a.sim_id, sim_b.sim_id)}_{max(sim_a.sim_id, sim_b.sim_id)}"
                restored_pair_memories = restored_engine.memory_store._store.get(
                    pair_key, []
                )
                self.assertTrue(
                    any(
                        m.get("text") == "Had a heartfelt conversation"
                        for m in restored_pair_memories
                    )
                )
                self.assertEqual(restored_engine.tick_count, 9)
            finally:
                db2.close()


if __name__ == "__main__":
    unittest.main()
