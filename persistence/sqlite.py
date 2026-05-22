from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from config import NEED_NAMES, SIM_DB_PATH
from core.emotions import EmotionState
from core.relationships import RelationshipRecord
from core.sentiments import SentimentRecord
from sim_types.enums import ControlMode
from sim_types.enums import LODTier
from sim_types.sim_types import Fear, Moodlet, Want
from world.households import Household

if TYPE_CHECKING:
    from core.sim import Sim
    from narrative.gossip import GossipGraph
    from engine.engine import SimEngine


_EVENT_BATCH_SIZE = 64   # flush event buffer after this many pending rows


class PersistenceLayer:
    def __init__(self, db_path: str = SIM_DB_PATH):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        # WAL mode — readers never block the writer; writer never blocks readers.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA wal_autocheckpoint=500")
        self._event_buffer: list[tuple[int, str, str, str]] = []
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
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                tick INTEGER,
                data TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def _to_jsonable(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Enum):
            return value.value
        if is_dataclass(value):
            return self._to_jsonable(asdict(value))
        if isinstance(value, dict):
            return {str(k): self._to_jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._to_jsonable(v) for v in value]
        return str(value)

    def _sim_payload(self, sim: "Sim") -> dict:
        excluded = {
            "profile",
            "needs",
            "emotion",
            "skills",
            "fears",
            "active_wants",
            "moodlets",
            "sim_id",
            "name",
        }
        attrs = {k: v for k, v in sim.__dict__.items() if k not in excluded}
        return {
            "sim_id": sim.sim_id,
            "name": sim.name,
            "profile": self._to_jsonable(sim.profile),
            "needs": {n: getattr(sim.needs, n) for n in NEED_NAMES},
            "emotion": {
                "dominant": sim.emotion.dominant,
                "dominant_valence": sim.emotion.dominant_valence,
                "moodlets": [self._to_jsonable(m) for m in sim.emotion.moodlets],
            },
            "skills": dict(sim.skills.levels),
            "fears": [self._to_jsonable(f) for f in sim.fears],
            "active_wants": [self._to_jsonable(w) for w in sim.active_wants],
            "sim_moodlets": self._to_jsonable(sim.moodlets.active())
            if hasattr(sim, "moodlets")
            else [],
            "attrs": self._to_jsonable(attrs),
        }

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
        """Buffer an event; flush automatically when the buffer is full."""
        self._event_buffer.append((tick, sim_id, event_type, json.dumps(data)))
        if len(self._event_buffer) >= _EVENT_BATCH_SIZE:
            self._flush_events()

    def _flush_events(self) -> None:
        if not self._event_buffer:
            return
        self.conn.executemany(
            "INSERT INTO events (tick, sim_id, event_type, data) VALUES (?,?,?,?)",
            self._event_buffer,
        )
        self._event_buffer.clear()

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
        self._flush_events()  # drain buffer before committing state
        for sim in engine.sims:
            self.save_sim(sim, engine.tick_count)
        for (a, b), rec in engine.relationships.all_pairs():
            self.save_relationship(a, b, rec, engine.tick_count)
        if hasattr(engine, "households"):
            for hh in engine.households:
                self.save_household(hh)
        if hasattr(engine, "gossip"):
            self.save_gossip(engine.gossip)

        relationships = []
        for (sim_a, sim_b), rec in engine.relationships.all_pairs():
            relationships.append(
                {
                    "sim_a": sim_a,
                    "sim_b": sim_b,
                    "record": self._to_jsonable(rec),
                }
            )

        snapshot = {
            "version": 1,
            "tick": engine.tick_count,
            "sims": [self._sim_payload(sim) for sim in engine.sims],
            "households": [self._to_jsonable(hh.__dict__) for hh in engine.households],
            "relationships": relationships,
            "memory_store": {
                "store": self._to_jsonable(getattr(engine.memory_store, "_store", {})),
                "long_term": self._to_jsonable(
                    getattr(engine.memory_store, "_long_term", {})
                ),
            },
            "gossip_facts": [
                {
                    "knower": knower,
                    "subject": subject,
                    "facts": self._to_jsonable(facts),
                }
                for (knower, subject), facts in getattr(
                    engine.gossip, "_facts", {}
                ).items()
            ],
        }
        self.conn.execute(
            "INSERT OR REPLACE INTO snapshots (id, tick, data) VALUES (1, ?, ?)",
            (engine.tick_count, json.dumps(snapshot)),
        )
        self.conn.commit()

    def load_state(self) -> dict | None:
        row = self.conn.execute("SELECT data FROM snapshots WHERE id = 1").fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            return None

    def restore_engine(self, engine: "SimEngine", state: dict) -> None:
        sim_lookup = {sim.sim_id: sim for sim in engine.sims}
        for sim_state in state.get("sims", []):
            sim = sim_lookup.get(sim_state.get("sim_id"))
            if sim is None:
                continue
            sim.profile = dict(sim_state.get("profile", sim.profile))
            sim.name = sim_state.get("name", sim.name)

            for need_name, value in sim_state.get("needs", {}).items():
                if hasattr(sim.needs, need_name):
                    setattr(sim.needs, need_name, float(value))

            sim.skills.levels = {
                str(k): float(v) for k, v in sim_state.get("skills", {}).items()
            }

            emotion_state = sim_state.get("emotion", {})
            sim.emotion = EmotionState()
            sim.emotion.dominant = str(emotion_state.get("dominant", "neutral"))
            sim.emotion.dominant_valence = float(
                emotion_state.get("dominant_valence", 0.5)
            )
            sim.emotion.moodlets = [
                Moodlet(
                    label=str(m.get("label", "neutral")),
                    intensity=float(m.get("intensity", 0.1)),
                    duration=int(m.get("duration", 1)),
                    source=str(m.get("source", "")),
                )
                for m in emotion_state.get("moodlets", [])
            ]

            sim.fears = [
                Fear(
                    label=str(f.get("label", "")),
                    severity=float(f.get("severity", 0.0)),
                )
                for f in sim_state.get("fears", [])
            ]
            sim.active_wants = [
                Want(
                    description=str(w.get("description", "")),
                    target_sim=w.get("target_sim"),
                    need_linked=w.get("need_linked"),
                    priority=float(w.get("priority", 0.0)),
                )
                for w in sim_state.get("active_wants", [])
            ]

            if hasattr(sim, "moodlets"):
                sim.moodlets._moodlets = []
                for moodlet in sim_state.get("sim_moodlets", []):
                    sim.moodlets.add(
                        str(moodlet.get("label", "")),
                        source=str(moodlet.get("source", "")),
                        override_duration=int(moodlet.get("duration", 1)),
                    )

            attrs = sim_state.get("attrs", {})
            for key, value in attrs.items():
                if key == "lod_tier":
                    try:
                        setattr(sim, key, LODTier(int(value)))
                    except Exception:
                        pass
                elif key == "control_mode":
                    try:
                        setattr(sim, key, ControlMode(str(value)))
                    except Exception:
                        pass
                elif key in {
                    "perks",
                    "reward_traits",
                    "death_traits",
                    "temporary_traits",
                    "formative_traits",
                    "hidden_traits",
                }:
                    setattr(sim, key, set(value or []))
                else:
                    setattr(sim, key, value)

        engine.relationships._pairs.clear()
        for rel in state.get("relationships", []):
            a = rel.get("sim_a")
            b = rel.get("sim_b")
            rec_data = rel.get("record", {})
            if not a or not b:
                continue
            rec = RelationshipRecord(
                friendship=float(rec_data.get("friendship", 0.0)),
                romance=float(rec_data.get("romance", 0.0)),
                interactions=int(rec_data.get("interactions", 0)),
                memories=list(rec_data.get("memories", [])),
            )
            rec.sentiments = [
                SentimentRecord(
                    name=str(s.get("name", "")),
                    added_tick=int(s.get("added_tick", 0)),
                    expires_tick=int(s.get("expires_tick", -1)),
                    source=str(s.get("source", "")),
                )
                for s in rec_data.get("sentiments", [])
                if isinstance(s, dict)
            ]
            rec.in_toxic_cycle = bool(rec_data.get("in_toxic_cycle", False))
            rec.toxic_cycle_phase = str(rec_data.get("toxic_cycle_phase", "none"))
            rec.toxic_cycle_tick = int(rec_data.get("toxic_cycle_tick", 0))
            rec.jealousy_score = float(rec_data.get("jealousy_score", 0.0))
            rec.mentor_of = str(rec_data.get("mentor_of", ""))
            engine.relationships._pairs[(min(a, b), max(a, b))] = rec

        engine.memory_store._store = dict(
            state.get("memory_store", {}).get("store", {})
        )
        engine.memory_store._long_term = dict(
            state.get("memory_store", {}).get("long_term", {})
        )

        engine.gossip._facts = {}
        for row in state.get("gossip_facts", []):
            if not isinstance(row, dict):
                continue
            knower = str(row.get("knower", "")).strip()
            subject = str(row.get("subject", "")).strip()
            if not knower or not subject:
                continue
            engine.gossip._facts[(knower, subject)] = row.get("facts", [])

        households = []
        for raw_hh in state.get("households", []):
            try:
                households.append(
                    Household(
                        id=str(raw_hh.get("id", "")),
                        name=str(raw_hh.get("name", "Household")),
                        member_ids=list(raw_hh.get("member_ids", [])),
                        funds=float(raw_hh.get("funds", 0.0)),
                        home_venue=raw_hh.get("home_venue"),
                    )
                )
            except Exception:
                continue
        if households:
            engine.households = households

        engine._tick_count = int(state.get("tick", 0))

    def close(self) -> None:
        self.conn.close()
