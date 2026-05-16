import json
import sqlite3

from config import NEED_NAMES, SIM_DB_PATH
from sim_types.enums import LODTier


class PersistenceLayer:
    def __init__(self, db_path: str = SIM_DB_PATH):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self) -> None:
        cursor = self.conn.cursor()
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS sims (
                id TEXT PRIMARY KEY, data TEXT, tick INTEGER
            );
            CREATE TABLE IF NOT EXISTS relationships (
                sim_a TEXT, sim_b TEXT, data TEXT, tick INTEGER,
                PRIMARY KEY (sim_a, sim_b)
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tick INTEGER, sim_id TEXT, event_type TEXT, data TEXT
            );
            CREATE TABLE IF NOT EXISTS households (
                id TEXT PRIMARY KEY, data TEXT
            );
            CREATE TABLE IF NOT EXISTS gossip (
                knower TEXT, subject TEXT, facts TEXT,
                PRIMARY KEY (knower, subject)
            );
            """
        )
        self.conn.commit()

    def save_sim(self, sim: "Sim", tick: int) -> None:
        payload = {
            "profile": dict(sim.profile),
            "needs": {n: getattr(sim.needs, n) for n in NEED_NAMES},
            "emotion": sim.emotion.dominant,
            "fears": [{"label": f.label, "severity": f.severity} for f in sim.fears],
            "skills": sim.skills.levels,
            "career_performance": sim.career_performance,
            "simoleons": sim.simoleons,
            "lod_tier": int(getattr(sim, "lod_tier", LODTier.ACTIVE)),
        }
        self.conn.execute(
            "INSERT OR REPLACE INTO sims VALUES (?,?,?)",
            (sim.sim_id, json.dumps(payload), tick),
        )

    def save_relationship(
        self, sim_a_id: str, sim_b_id: str, rec: "RelationshipRecord", tick: int
    ) -> None:
        a, b = min(sim_a_id, sim_b_id), max(sim_a_id, sim_b_id)
        payload = {
            "friendship": rec.friendship,
            "romance": rec.romance,
            "interactions": rec.interactions,
            "memories": rec.memories[-10:],
        }
        self.conn.execute(
            "INSERT OR REPLACE INTO relationships VALUES (?,?,?,?)",
            (a, b, json.dumps(payload), tick),
        )

    def log_event(self, tick: int, sim_id: str, event_type: str, data: dict) -> None:
        self.conn.execute(
            "INSERT INTO events (tick, sim_id, event_type, data) VALUES (?,?,?,?)",
            (tick, sim_id, event_type, json.dumps(data)),
        )

    def save_household(self, hh: "Household") -> None:
        payload = {
            "name": hh.name,
            "member_ids": hh.member_ids,
            "funds": hh.funds,
        }
        self.conn.execute(
            "INSERT OR REPLACE INTO households VALUES (?,?)",
            (hh.id, json.dumps(payload)),
        )

    def save_gossip(self, graph: "GossipGraph") -> None:
        self.conn.execute("DELETE FROM gossip")
        for (knower, subject), facts in graph._facts.items():
            self.conn.execute(
                "INSERT INTO gossip VALUES (?,?,?)",
                (knower, subject, json.dumps(facts)),
            )

    def load_gossip(self, graph_class: type) -> "GossipGraph":
        g = graph_class()
        for row in self.conn.execute("SELECT knower, subject, facts FROM gossip"):
            g._facts[(row[0], row[1])] = json.loads(row[2])
        return g

    def save_state(self, engine: "SimEngine") -> None:
        for sim in engine.sims:
            self.save_sim(sim, engine.tick_count)
        for (a, b), rec in engine.relationships.all_pairs():
            self.save_relationship(a, b, rec, engine.tick_count)
        if hasattr(engine, "households"):
            for hh in engine.households:
                self.save_household(hh)
        if hasattr(engine, "gossip"):
            self.save_gossip(engine.gossip)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
