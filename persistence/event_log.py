"""
persistence/event_log.py — Snapshot + event-sourcing hybrid log.

Schema
------
  world_snapshots  — periodic full world state (every SNAPSHOT_INTERVAL ticks)
  event_deltas     — append-only state mutations since last snapshot

Recovery
--------
  1. Load latest snapshot  →  restore base world state.
  2. Replay deltas since snapshot tick  →  fast-forward to current state.

This gives sub-second cold-start recovery and cheap horizontal replication:
replicas subscribe to the delta stream and apply incrementally.

WAL mode is enforced so concurrent readers never block the writer.
Writes are buffered and flushed in batches to reduce fsync overhead.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FLUSH_INTERVAL = 50   # buffer entries before forcing a flush


class EventLog:
    def __init__(self, db_path: str) -> None:
        self._path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA wal_autocheckpoint=1000")
        self._init_schema()
        self._buffer: list[tuple[int, str, str, str]] = []  # (tick, sim_id, type, json)

    # ── Schema ────────────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS world_snapshots (
                tick  INTEGER PRIMARY KEY,
                ts    REAL    NOT NULL,
                data  TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS event_deltas (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                tick     INTEGER NOT NULL,
                ts       REAL    NOT NULL,
                sim_id   TEXT    NOT NULL DEFAULT '',
                evt_type TEXT    NOT NULL,
                delta    TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_deltas_tick
                ON event_deltas(tick);
        """)
        self._conn.commit()

    # ── Snapshot API ──────────────────────────────────────────────────────────

    def write_snapshot(self, tick: int, world_state: dict) -> None:
        with self._lock:
            self._flush_locked()
            self._conn.execute(
                "INSERT OR REPLACE INTO world_snapshots(tick, ts, data) VALUES (?,?,?)",
                (tick, time.time(), json.dumps(world_state, default=str)),
            )
            self._conn.commit()
        logger.debug("[EventLog] snapshot at tick %d", tick)

    def latest_snapshot(self) -> tuple[int, dict] | None:
        row = self._conn.execute(
            "SELECT tick, data FROM world_snapshots ORDER BY tick DESC LIMIT 1"
        ).fetchone()
        if row:
            return row[0], json.loads(row[1])
        return None

    # ── Delta API ─────────────────────────────────────────────────────────────

    def append(self, tick: int, sim_id: str, evt_type: str, delta: Any) -> None:
        """Buffer a state delta; auto-flush when buffer is full."""
        payload = json.dumps(delta, default=str)
        with self._lock:
            self._buffer.append((tick, sim_id or "", evt_type, payload))
            if len(self._buffer) >= _FLUSH_INTERVAL:
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        now = time.time()
        self._conn.executemany(
            "INSERT INTO event_deltas(tick,ts,sim_id,evt_type,delta) VALUES(?,?,?,?,?)",
            [(t, now, s, e, d) for t, s, e, d in self._buffer],
        )
        self._conn.commit()
        self._buffer.clear()

    def deltas_since(self, tick: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT tick, sim_id, evt_type, delta "
            "FROM event_deltas WHERE tick > ? ORDER BY id",
            (tick,),
        ).fetchall()
        return [
            {"tick": r[0], "sim_id": r[1], "evt_type": r[2], "delta": json.loads(r[3])}
            for r in rows
        ]

    # ── Recovery ──────────────────────────────────────────────────────────────

    def recover(self) -> tuple[int, dict, list[dict]]:
        """
        Returns (base_tick, snapshot_state, deltas_since_snapshot).
        If no snapshot exists returns (0, {}, []).
        """
        snap = self.latest_snapshot()
        if snap is None:
            return 0, {}, []
        base_tick, state = snap
        deltas = self.deltas_since(base_tick)
        return base_tick, state, deltas

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        self.flush()
        self._conn.close()
