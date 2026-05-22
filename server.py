#!/usr/bin/env python3
"""
server.py — FastAPI REST + WebSocket server for the Sims Engine.

Start:
    pip install fastapi uvicorn
    python server.py                     # 3 sims, port 8080
    python server.py --sims 5 --port 8080

Endpoints:
    GET  /state             current world state
    POST /tick              advance one tick, returns world state
    GET  /sim/{id}          single sim detail with gossip
    GET  /profile/{id}      full social profile (identity, personality, relationships, activity)
    POST /interact          force a specific interaction
    POST /items/buy         buy item from lot stock
    POST /items/sell        sell item from sim inventory
    POST /items/gift        gift item between sims
    POST /items/use         consume/use item from inventory
    POST /items/trade       trade item sim-to-sim for simoleons
    POST /dynasty/create    create a dynasty
    POST /dynasty/heir      set dynasty heir
    POST /dynasty/outcast   mark member outcast
    POST /dynasty/perk      spend perk points
    POST /dynasty/alliance  set alliance between dynasties
    POST /dynasty/rivalry   set rivalry between dynasties
    GET  /items/lot/{lot_id}  list lot stock
    GET  /items/search        search global item catalog
    GET  /lot/{lot_id}/layout   room-by-room placement map with passive effects
    GET  /lot/{lot_id}/ambiance aggregated need bonuses per tick
    POST /lot/{lot_id}/place    move item from inventory into a home zone
    POST /lot/{lot_id}/remove   return placed item to sim inventory
    GET  /grim/status       Grim Reaper presence, linger timer, tombstones
    POST /grim/plead        plead with Grim to spare a dying Sim
    POST /grim/chess        challenge Grim to chess (logic-skill gated)
    POST /grim/pet_save     pet harasses Grim to save their master
    GET  /grim/tombstones   list all tombstones (filter: ?lot_id=)
    GET  /burglar/status    burglar state and cooldown
    GET  /burglar/log       recent burglary events
    POST /burglar/trigger   force burglary event (optional lot_id)
    GET  /investments/{sim_id}  investment dashboard for sim
    POST /investments/buy    buy property investment
    POST /investments/collect collect property income/dividend
    POST /investments/upgrade upgrade owned property
    POST /investments/rename rename owned business
    POST /investments/employee hire/fire employee
    POST /investments/sell   sell owned property
    DELETE /reset           restart the simulation
    WS   /stream            WebSocket: pushes state JSON after every tick
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse
    import uvicorn
except ImportError:
    print("Install fastapi + uvicorn: pip install fastapi uvicorn")
    sys.exit(1)

from engine.engine import SimEngine
from engine.scheduler import choose_interaction
from identity.profile_factory import generate_sim_profile
from core.sim import Sim
from datasets.loader import load_all_datasets
from llm.backend import create_backend
from llm.timing import store as timing_store, TimedBackend
from persistence.sqlite import PersistenceLayer
from world.households import assign_households
from config import MARKET_SHOPS

# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Sims Engine API", version="2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_engine: SimEngine | None = None
_engine_lock = threading.Lock()
_ws_clients: list[WebSocket] = []
_online_ws_clients: list[WebSocket] = []
_online_broadcast_loop = None
_online_world_task: asyncio.Task | None = None
_args: argparse.Namespace = argparse.Namespace(
    sims=3, port=8080, host="0.0.0.0", backend="llama-server", no_datasets=False
)


@dataclass
class PlayerSession:
    player_id: str
    username: str
    token: str
    room_id: str = "global"
    sim_id: str | None = None
    connected: bool = False
    last_seen: float = field(default_factory=lambda: time.time())
    created_at: float = field(default_factory=lambda: time.time())


class OnlineWorld:
    def __init__(self) -> None:
        self.sessions_by_token: dict[str, PlayerSession] = {}
        self.sessions_by_player: dict[str, PlayerSession] = {}
        self.sessions_by_username: dict[str, PlayerSession] = {}
        self.room_members: dict[str, set[str]] = {"global": set()}
        self.sim_owner: dict[str, str] = {}
        self._chat_log: list[dict] = []
        self.room_meta: dict[str, dict] = {
            "global": {"kind": "social", "allow": {"chat", "interact", "move"}},
            "shopping_center": {
                "kind": "shop",
                "allow": {
                    "chat",
                    "interact",
                    "move",
                    "buy",
                    "sell",
                    "use",
                    "gift",
                    "trade",
                    "adopt_pet",
                    "buy_pet",
                    "feed_pet",
                    "pet_pet",
                    "play_pet",
                    "refill_pet_bowl",
                },
            },
            "home": {
                "kind": "home",
                "allow": {
                    "chat",
                    "interact",
                    "move",
                    "use",
                    "gift",
                    "trade",
                    "feed_pet",
                    "pet_pet",
                    "play_pet",
                    "refill_pet_bowl",
                },
            },
            "park": {
                "kind": "social",
                "allow": {"chat", "interact", "move", "gift", "trade"},
            },
            "nightclub": {
                "kind": "social",
                "allow": {"chat", "interact", "move", "gift", "trade"},
            },
        }
        self.session_ttl_s: float = 6 * 3600.0
        self._archived_sessions: list[dict] = []
        for shop in MARKET_SHOPS:
            lid = str(shop.get("lot_id", "")).strip()
            if not lid:
                continue
            self.room_meta[lid] = {
                "kind": "shop",
                "shop_name": shop.get("name", lid),
                "allow": {
                    "chat",
                    "interact",
                    "move",
                    "buy",
                    "sell",
                    "use",
                    "gift",
                    "trade",
                    "adopt_pet",
                    "buy_pet",
                    "feed_pet",
                    "pet_pet",
                    "play_pet",
                    "refill_pet_bowl",
                },
            }

    def create_session(self, username: str) -> PlayerSession:
        uname = username.strip().lower()
        if uname in self.sessions_by_username:
            return self.sessions_by_username[uname]
        pid = f"p_{uuid.uuid4().hex[:8]}"
        token = f"tok_{uuid.uuid4().hex}"
        s = PlayerSession(player_id=pid, username=username.strip()[:32], token=token)
        self.sessions_by_token[token] = s
        self.sessions_by_player[pid] = s
        self.sessions_by_username[uname] = s
        self.room_members.setdefault("global", set()).add(pid)
        return s

    def get_session(self, token: str) -> PlayerSession | None:
        return self.sessions_by_token.get(token)

    def touch(self, session: PlayerSession, connected: bool | None = None) -> None:
        session.last_seen = time.time()
        if connected is not None:
            session.connected = bool(connected)

    def join_room(self, session: PlayerSession, room_id: str) -> None:
        if room_id not in self.room_meta:
            self.room_meta[room_id] = {
                "kind": "custom",
                "allow": {"chat", "interact", "move", "gift", "trade", "use"},
            }
        old = session.room_id
        self.room_members.setdefault(old, set()).discard(session.player_id)
        session.room_id = room_id
        self.room_members.setdefault(room_id, set()).add(session.player_id)

    def claim_sim(self, session: PlayerSession, sim_id: str) -> bool:
        owner = self.sim_owner.get(sim_id)
        if owner is not None and owner != session.player_id:
            return False
        self.sim_owner[sim_id] = session.player_id
        session.sim_id = sim_id
        return True

    def is_owner(self, session: PlayerSession, sim_id: str) -> bool:
        return self.sim_owner.get(sim_id) == session.player_id

    def room_sim_ids(self, eng: SimEngine, room_id: str) -> list[str]:
        members = self.room_members.get(room_id, set())
        out = []
        for pid in members:
            sess = self.sessions_by_player.get(pid)
            if sess and sess.sim_id and any(s.sim_id == sess.sim_id for s in eng.sims):
                out.append(sess.sim_id)
        return out

    def push_chat(self, room_id: str, player_id: str, text: str) -> dict:
        evt = {
            "type": "chat",
            "room_id": room_id,
            "player_id": player_id,
            "text": text[:240],
            "ts": round(time.time(), 3),
        }
        self._chat_log.append(evt)
        self._chat_log = self._chat_log[-300:]
        return evt

    def room_payload(self, eng: SimEngine, room_id: str) -> dict:
        sim_ids = self.room_sim_ids(eng, room_id)
        world = eng.get_state()
        sims = [s for s in world.get("sims", []) if s.get("id") in sim_ids]
        events = [e for e in self._chat_log if e.get("room_id") == room_id][-60:]
        return {
            "type": "room_state",
            "room_id": room_id,
            "room_meta": dict(self.room_meta.get(room_id, {})),
            "tick": world.get("tick"),
            "venue": world.get("venue"),
            "sims": sims,
            "events": events,
            "market": world.get("market", {}),
        }

    def cleanup_expired_sessions(self) -> dict:
        now = time.time()
        expired_tokens = []
        for token, sess in self.sessions_by_token.items():
            if sess.connected:
                continue
            if now - sess.last_seen > self.session_ttl_s:
                expired_tokens.append(token)
        for token in expired_tokens:
            sess = self.sessions_by_token.pop(token, None)
            if not sess:
                continue
            self.sessions_by_player.pop(sess.player_id, None)
            self.sessions_by_username.pop(sess.username.strip().lower(), None)
            self.room_members.setdefault(sess.room_id, set()).discard(sess.player_id)
            self._archived_sessions.append(
                {
                    "player_id": sess.player_id,
                    "username": sess.username,
                    "sim_id": sess.sim_id,
                    "expired_at": round(now, 3),
                }
            )
        self._archived_sessions = self._archived_sessions[-200:]
        return {"expired": len(expired_tokens)}

    def session_stats(self) -> dict:
        active = sum(1 for s in self.sessions_by_token.values() if s.connected)
        return {
            "total": len(self.sessions_by_token),
            "connected": active,
            "disconnected": max(0, len(self.sessions_by_token) - active),
            "ttl_seconds": self.session_ttl_s,
            "archived_count": len(self._archived_sessions),
        }


_online_world = OnlineWorld()

# ── Auth store (lazy-initialised on first use / startup) ──────────────────────
from persistence.auth import AuthStore as _AuthStore
_auth_store: _AuthStore | None = None


def _get_auth() -> _AuthStore:
    global _auth_store
    if _auth_store is None:
        _auth_store = _AuthStore("sim_auth.db")
    return _auth_store


def _require_auth(token: str):
    """Raise 401 if token is invalid. Returns the User on success."""
    user = _get_auth().verify_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")
    return user


def _get_engine() -> SimEngine:
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not initialised")
    return _engine


def _build_engine(num_sims: int) -> SimEngine:
    timing_store.start_boot()

    datasets = None
    essays = []
    if not _args.no_datasets:
        with timing_store.phase("datasets"):
            datasets = load_all_datasets()
            essays = datasets.okcupid_essays

    with timing_store.phase("llm_init"):
        raw_llm = create_backend(_args.backend)
    llm_name = f"{_args.backend}/{getattr(raw_llm, '_model', _args.backend)}"
    llm = TimedBackend(raw_llm, name=llm_name)

    with timing_store.phase("profiles"):
        sims = [
            Sim(generate_sim_profile(okcupid_essays=essays or None))
            for _ in range(num_sims)
        ]

    with timing_store.phase("households"):
        households = assign_households(sims)

    with timing_store.phase("persistence"):
        db = PersistenceLayer()
        snapshot = None if getattr(_args, "no_restore", False) else db.load_state()

    if snapshot and snapshot.get("sims"):
        restored_sims = []
        for sim_state in snapshot.get("sims", []):
            profile = sim_state.get("profile")
            if isinstance(profile, dict) and profile.get("id"):
                restored_sims.append(Sim(profile))
        if restored_sims:
            sims = restored_sims
            households = assign_households(sims)

    with timing_store.phase("engine_init"):
        engine = SimEngine(sims=sims, llm=llm, datasets=datasets, db=db)
        engine.households = households
        if snapshot and snapshot.get("sims"):
            db.restore_engine(engine, snapshot)

    boot_total = timing_store.finish_boot()
    timing_store.print_boot()
    return engine


# ─── REST endpoints ──────────────────────────────────────────────────────────


@app.get("/state")
def get_state():
    return _get_engine().get_state()


@app.post("/online/session")
def create_online_session(body: dict):
    username = str(body.get("username", "")).strip()
    if not username:
        raise HTTPException(status_code=400, detail="username required")
    s = _online_world.create_session(username)
    return {
        "ok": True,
        "player_id": s.player_id,
        "token": s.token,
        "room_id": s.room_id,
    }


@app.post("/online/connect")
def online_connect(body: dict):
    """Attach client to persistent server-side session. Body: {token?|username, sim_id?}"""
    token = str(body.get("token", "")).strip()
    username = str(body.get("username", "")).strip()
    sim_id = str(body.get("sim_id", "")).strip()

    sess = _online_world.get_session(token) if token else None
    if sess is None and username:
        sess = _online_world.create_session(username)
    if sess is None:
        raise HTTPException(status_code=400, detail="token or username required")

    if sim_id:
        eng = _get_engine()
        if not any(s.sim_id == sim_id for s in eng.sims):
            raise HTTPException(status_code=404, detail="sim not found")
        owner = _online_world.sim_owner.get(sim_id)
        if owner is not None and owner != sess.player_id:
            raise HTTPException(status_code=409, detail="sim already claimed")
        _online_world.claim_sim(sess, sim_id)

    _online_world.touch(sess, connected=True)
    return {
        "ok": True,
        "player_id": sess.player_id,
        "token": sess.token,
        "room_id": sess.room_id,
        "sim_id": sess.sim_id,
        "connected": sess.connected,
    }


@app.post("/online/disconnect")
def online_disconnect(body: dict):
    token = str(body.get("token", "")).strip()
    sess = _online_world.get_session(token)
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")
    _online_world.touch(sess, connected=False)
    return {"ok": True, "player_id": sess.player_id, "connected": sess.connected}


@app.get("/online/sessions")
def online_sessions():
    return _online_world.session_stats()


@app.post("/online/sessions/ttl")
def online_sessions_ttl(body: dict):
    ttl = float(body.get("ttl_seconds", 0))
    if ttl <= 0:
        raise HTTPException(status_code=400, detail="ttl_seconds must be > 0")
    _online_world.session_ttl_s = ttl
    return {"ok": True, "ttl_seconds": _online_world.session_ttl_s}


@app.post("/online/session/resume")
def resume_online_session(body: dict):
    token = str(body.get("token", "")).strip()
    username = str(body.get("username", "")).strip().lower()
    sess = _online_world.get_session(token) if token else None
    if sess is None and username:
        sess = _online_world.sessions_by_username.get(username)
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {
        "ok": True,
        "player_id": sess.player_id,
        "token": sess.token,
        "room_id": sess.room_id,
        "sim_id": sess.sim_id,
    }


@app.post("/online/claim")
def claim_sim(body: dict):
    token = str(body.get("token", ""))
    sim_id = str(body.get("sim_id", ""))
    sess = _online_world.get_session(token)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid token")
    eng = _get_engine()
    if not any(s.sim_id == sim_id for s in eng.sims):
        raise HTTPException(status_code=404, detail="sim not found")
    ok = _online_world.claim_sim(sess, sim_id)
    if not ok:
        raise HTTPException(status_code=409, detail="sim already claimed")
    return {"ok": True, "player_id": sess.player_id, "sim_id": sim_id}


@app.post("/online/room/join")
def join_room(body: dict):
    token = str(body.get("token", ""))
    room_id = str(body.get("room_id", "")).strip()
    if not room_id:
        raise HTTPException(status_code=400, detail="room_id required")
    sess = _online_world.get_session(token)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid token")
    _online_world.join_room(sess, room_id)
    return {"ok": True, "room_id": room_id}


@app.post("/online/action")
def online_action(body: dict):
    token = str(body.get("token", ""))
    command = str(body.get("command", ""))
    args = body.get("args", {}) if isinstance(body.get("args", {}), dict) else {}
    sess = _online_world.get_session(token)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid token")
    eng = _get_engine()
    if not sess.sim_id:
        raise HTTPException(status_code=400, detail="no claimed sim")
    sim = next((s for s in eng.sims if s.sim_id == sess.sim_id), None)
    if sim is None:
        raise HTTPException(status_code=404, detail="claimed sim missing")
    if not _online_world.is_owner(sess, sim.sim_id):
        raise HTTPException(status_code=403, detail="not owner")

    with _engine_lock:
        room_allow = set(
            _online_world.room_meta.get(sess.room_id, {}).get("allow", set())
        )
        if command not in room_allow:
            raise HTTPException(
                status_code=403,
                detail=f"command '{command}' not allowed in room '{sess.room_id}'",
            )

        # Basic tamagotchi-style readiness hints
        energy = float(getattr(sim.needs, "energy", 100.0))
        if command in {"interact", "trade", "gift"} and energy < 10:
            raise HTTPException(status_code=409, detail="too_tired_for_social_action")

        if command == "chat":
            text = str(args.get("text", "")).strip()
            if not text:
                raise HTTPException(status_code=400, detail="text required")
            evt = _online_world.push_chat(sess.room_id, sess.player_id, text)
            return {"ok": True, "event": evt}

        if command == "move":
            room_id = str(args.get("room_id", "")).strip()
            if not room_id:
                raise HTTPException(status_code=400, detail="room_id required")
            _online_world.join_room(sess, room_id)
            return {"ok": True, "room_id": room_id}

        if command == "buy":
            default_lot = (
                sess.room_id
                if sess.room_id in _online_world.room_meta
                else "shopping_center"
            )
            lot_id = str(args.get("lot_id", default_lot))
            object_id = int(args.get("object_id", -1))
            qty = int(args.get("qty", 1))
            if object_id < 0:
                raise HTTPException(status_code=400, detail="object_id required")
            return eng.buy_item(sim.sim_id, lot_id, object_id, qty=qty)

        if command == "sell":
            object_id = int(args.get("object_id", -1))
            if object_id < 0:
                raise HTTPException(status_code=400, detail="object_id required")
            return eng.sell_item(sim.sim_id, object_id, qty=int(args.get("qty", 1)))

        if command == "use":
            object_id = int(args.get("object_id", -1))
            if object_id < 0:
                raise HTTPException(status_code=400, detail="object_id required")
            return eng.use_item(sim.sim_id, object_id)

        if command == "gift":
            target_sim_id = str(args.get("target_sim_id", ""))
            if not target_sim_id:
                raise HTTPException(status_code=400, detail="target_sim_id required")
            oid = args.get("object_id")
            return eng.gift_item(
                sim.sim_id, target_sim_id, int(oid) if oid is not None else None
            )

        if command == "trade":
            target_sim_id = str(args.get("target_sim_id", ""))
            object_id = int(args.get("object_id", -1))
            if not target_sim_id or object_id < 0:
                raise HTTPException(
                    status_code=400, detail="target_sim_id and object_id required"
                )
            return eng.trade_item(
                from_sim_id=sim.sim_id,
                to_sim_id=target_sim_id,
                object_id=object_id,
                qty=int(args.get("qty", 1)),
                unit_price=float(args.get("unit_price"))
                if args.get("unit_price") is not None
                else None,
            )

        if command == "interact":
            target_sim_id = str(args.get("target_sim_id", ""))
            action = str(args.get("action", "chat"))
            target = next((s for s in eng.sims if s.sim_id == target_sim_id), None)
            if target is None:
                raise HTTPException(status_code=404, detail="target sim not found")
            eng._submit_interaction(sim, target, action, eng._venue)
            return {"ok": True, "queued": True, "action": action}

        if command == "adopt_pet":
            species = args.get("species")
            return eng.adopt_pet(sim.sim_id, species=str(species) if species else None)

        if command == "buy_pet":
            species = args.get("species")
            return eng.buy_pet(sim.sim_id, species=str(species) if species else None)

        if command == "feed_pet":
            pet_id = str(args.get("pet_id", ""))
            if not pet_id:
                raise HTTPException(status_code=400, detail="pet_id required")
            return eng.feed_pet(sim.sim_id, pet_id)

        if command == "pet_pet":
            pet_id = str(args.get("pet_id", ""))
            if not pet_id:
                raise HTTPException(status_code=400, detail="pet_id required")
            return eng.pet_pet(sim.sim_id, pet_id)

        if command == "play_pet":
            pet_id = str(args.get("pet_id", ""))
            if not pet_id:
                raise HTTPException(status_code=400, detail="pet_id required")
            return eng.play_with_pet(sim.sim_id, pet_id)

        if command == "refill_pet_bowl":
            lot_id = str(args.get("lot_id", ""))
            if not lot_id:
                raise HTTPException(status_code=400, detail="lot_id required")
            return eng.refill_pet_bowl(sim.sim_id, lot_id)

    raise HTTPException(status_code=400, detail="unknown command")


@app.get("/online/room/{room_id}")
def room_state(room_id: str):
    return _online_world.room_payload(_get_engine(), room_id)


@app.get("/shops")
def list_shops():
    eng = _get_engine()
    shops = []
    for shop in MARKET_SHOPS:
        lot_id = str(shop.get("lot_id", ""))
        if not lot_id:
            continue
        shops.append(
            {
                "lot_id": lot_id,
                "name": shop.get("name", lot_id),
                "focus": list(shop.get("focus", [])),
                "stock_count": len(eng.objects.lot_object_stock.get(lot_id, {})),
            }
        )
    return {"shops": shops, "count": len(shops)}


@app.get("/pets/catalog")
def pet_catalog():
    eng = _get_engine()
    return {"pets": eng.list_pet_catalog(), "count": len(eng.list_pet_catalog())}


@app.get("/pets/{sim_id}")
def sim_pets(sim_id: str):
    eng = _get_engine()
    sim = next((s for s in eng.sims if s.sim_id == sim_id), None)
    if sim is None:
        raise HTTPException(status_code=404, detail="sim not found")
    pets = list(getattr(sim, "pet_records", {}).values())
    out = [eng.pets._pet_state(p) for p in pets]
    return {"sim_id": sim_id, "pets": out, "count": len(out)}


@app.post("/pets/adopt")
def adopt_pet(body: dict):
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    species = body.get("species")
    if not sim_id:
        raise HTTPException(status_code=400, detail="sim_id required")
    with _engine_lock:
        res = eng.adopt_pet(sim_id, species=str(species) if species else None)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("reason", "adopt_failed"))
    return res


@app.post("/pets/buy")
def buy_pet(body: dict):
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    species = body.get("species")
    if not sim_id:
        raise HTTPException(status_code=400, detail="sim_id required")
    with _engine_lock:
        res = eng.buy_pet(sim_id, species=str(species) if species else None)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("reason", "buy_pet_failed"))
    return res


@app.post("/pets/feed")
def feed_pet(body: dict):
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    pet_id = str(body.get("pet_id", ""))
    if not sim_id or not pet_id:
        raise HTTPException(status_code=400, detail="sim_id and pet_id required")
    with _engine_lock:
        res = eng.feed_pet(sim_id, pet_id)
    if not res.get("ok"):
        raise HTTPException(
            status_code=400, detail=res.get("reason", "feed_pet_failed")
        )
    return res


@app.post("/pets/pet")
def pet_pet(body: dict):
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    pet_id = str(body.get("pet_id", ""))
    if not sim_id or not pet_id:
        raise HTTPException(status_code=400, detail="sim_id and pet_id required")
    with _engine_lock:
        res = eng.pet_pet(sim_id, pet_id)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("reason", "pet_pet_failed"))
    return res


@app.post("/pets/play")
def play_pet(body: dict):
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    pet_id = str(body.get("pet_id", ""))
    if not sim_id or not pet_id:
        raise HTTPException(status_code=400, detail="sim_id and pet_id required")
    with _engine_lock:
        res = eng.play_with_pet(sim_id, pet_id)
    if not res.get("ok"):
        raise HTTPException(
            status_code=400, detail=res.get("reason", "play_pet_failed")
        )
    return res


@app.post("/pets/bowl/refill")
def refill_pet_bowl(body: dict):
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    lot_id = str(body.get("lot_id", ""))
    if not sim_id or not lot_id:
        raise HTTPException(status_code=400, detail="sim_id and lot_id required")
    with _engine_lock:
        res = eng.refill_pet_bowl(sim_id, lot_id)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("reason", "refill_failed"))
    return res


@app.get("/pets/bowl/{lot_id}")
def pet_bowl_state(lot_id: str):
    eng = _get_engine()
    return eng.pets.bowl_state(lot_id)


def _action_hints_for_sim(sim_state: dict) -> dict:
    needs = sim_state.get("needs", {}) if isinstance(sim_state, dict) else {}
    energy = (
        float(needs.get("energy", 100.0))
        if isinstance(needs.get("energy", 100.0), (int, float))
        else float(needs.get("energy", {}).get("value", 100.0))
    )
    hunger = (
        float(needs.get("hunger", 100.0))
        if isinstance(needs.get("hunger", 100.0), (int, float))
        else float(needs.get("hunger", {}).get("value", 100.0))
    )
    social = (
        float(needs.get("social", 100.0))
        if isinstance(needs.get("social", 100.0), (int, float))
        else float(needs.get("social", {}).get("value", 100.0))
    )
    hints = []
    if energy < 25:
        hints.append("Low energy: prefer rest/use item before social actions")
    if hunger < 30:
        hints.append("Low hunger: buy/use food-oriented item")
    if social < 30:
        hints.append("Low social: use chat/interact actions")
    cooldowns = sim_state.get("action_cooldowns", {})
    return {"messages": hints[:3], "cooldowns": cooldowns}


@app.get("/timings")
def get_timings():
    return timing_store.summary()


@app.post("/tick")
def tick():
    eng = _get_engine()
    # Capture LLM call count before the tick so we can detect new calls
    llm_before = len(list(timing_store._llm))
    t0 = time.monotonic()
    with _engine_lock:
        eng.heartbeat.beat_once()
    tick_elapsed = time.monotonic() - t0
    timing_store.record_tick(eng.tick_count, tick_elapsed)
    # Surface the most recent LLM call if one fired during this tick
    llm_calls_this_tick = len(list(timing_store._llm)) - llm_before
    llm_recent = list(timing_store._llm)[-1] if llm_calls_this_tick > 0 else None
    llm_elapsed = llm_recent["elapsed_s"] if llm_recent else None
    timing_store.print_tick(eng.tick_count, tick_elapsed, llm_elapsed)
    return eng.get_state()


@app.get("/sim/{sim_id}")
def get_sim(sim_id: str):
    eng = _get_engine()
    sim = next((s for s in eng.sims if s.sim_id == sim_id), None)
    if sim is None:
        raise HTTPException(status_code=404, detail=f"Sim {sim_id} not found")
    state = eng.get_state()
    sim_state = next(s for s in state["sims"] if s["id"] == sim_id)
    sim_state["gossip"] = {
        other.sim_id: eng.gossip.recall(sim_id, other.sim_id)
        for other in eng.sims
        if other.sim_id != sim_id and eng.gossip.recall(sim_id, other.sim_id)
    }
    return sim_state


def _build_inventory_profile(sim, eng) -> dict:
    """Enrich inventory_objects with computed use-effects, sell values, and capacity stats."""
    objects = list(getattr(sim, "inventory_objects", []))
    slot_limits = dict(
        getattr(sim, "inventory_slot_limits", {"hand": 2, "body": 1, "utility": 9})
    )

    # Per-slot usage counts
    slot_counts: dict[str, int] = {}
    total_weight = 0.0
    items_out = []
    for obj in objects:
        slot = str(obj.get("slot", "utility"))
        weight = float(obj.get("weight", 0.0))
        slot_counts[slot] = slot_counts.get(slot, 0) + 1
        total_weight += weight

        effect = eng.objects._effect_for_item(obj)
        sell_value = round(float(obj.get("market_price", 0.0)) * 0.55, 2)

        items_out.append(
            {
                "id": obj.get("id"),
                "name": obj.get("name"),
                "type": obj.get("type"),
                "sub_type": obj.get("sub_type"),
                "rarity": obj.get("rarity"),
                "slot": slot,
                "weight": weight,
                "market_price": obj.get("market_price"),
                "sell_value": sell_value,
                "tradable": obj.get("tradable", False),
                "use_effect": {
                    "need": effect.get("need"),
                    "restore": effect.get("restore"),
                    "need2": effect.get("need2"),
                    "restore2": effect.get("restore2"),
                    "neg_need": effect.get("neg_need"),
                    "neg_amount": effect.get("neg_amount"),
                    "career_bonus": effect.get("career_bonus"),
                    "skill_xp": effect.get("skill_xp"),
                    "skill_amount": effect.get("skill_amount"),
                    "emotion": effect.get("emotion"),
                    "intensity": effect.get("intensity"),
                },
            }
        )

    # Sort: hand > body > utility, then rarity desc
    _rarity_order = {"legendary": 0, "epic": 1, "rare": 2, "uncommon": 3, "common": 4}
    _slot_order = {"hand": 0, "body": 1, "utility": 2}
    items_out.sort(
        key=lambda x: (
            _slot_order.get(x["slot"], 9),
            _rarity_order.get(x["rarity"], 9),
        )
    )

    return {
        "items": items_out,
        "capacity": {
            "slots_used": len(objects),
            "slots_max": int(getattr(sim, "inventory_max_slots", 12)),
            "weight_used": round(total_weight, 2),
            "weight_max": float(getattr(sim, "inventory_max_weight", 24.0)),
            "by_slot": {
                slot: {"used": slot_counts.get(slot, 0), "max": limit}
                for slot, limit in slot_limits.items()
            },
        },
    }


@app.get("/profile/{sim_id}")
def get_profile(sim_id: str):
    """Detailed social profile for a sim — identity, personality, relationships, activity."""
    eng = _get_engine()
    sim = next((s for s in eng.sims if s.sim_id == sim_id), None)
    if sim is None:
        raise HTTPException(status_code=404, detail=f"Sim {sim_id} not found")

    from core.compatibility import attraction_score
    from core.traits import active_traits
    from core.sentiments import SENTIMENT_CATALOGUE

    # ── Relationships (rich) ──────────────────────────────────────────────────
    relationships = {}
    total_interactions = 0
    for other in eng.sims:
        if other.sim_id == sim_id:
            continue
        rec = eng.relationships.get(sim_id, other.sim_id)
        total_interactions += rec.interactions
        attraction = 0.0
        try:
            attraction = round(attraction_score(sim, other), 3)
        except Exception:
            pass
        sentiments = []
        for s in rec.sentiments:
            cat = SENTIMENT_CATALOGUE.get(s.name, None)
            sentiments.append(
                {
                    "name": s.name,
                    "added_tick": s.added_tick,
                    "expires_tick": s.expires_tick,
                    "source": s.source,
                    "valence": cat.valence if cat else None,
                    "permanent": s.expires_tick == -1,
                }
            )
        relationships[other.sim_id] = {
            "name": other.name,
            "friendship": round(rec.friendship, 1),
            "romance": round(rec.romance, 1),
            "state": rec.state_label(),
            "romance_label": rec.romance_label(),
            "interactions": rec.interactions,
            "attraction_score": attraction,
            "jealousy_score": round(rec.jealousy_score, 1),
            "in_toxic_cycle": rec.in_toxic_cycle,
            "toxic_cycle_phase": rec.toxic_cycle_phase,
            "mentor_of": rec.mentor_of,
            "sentiments": sentiments,
            "memories": list(rec.memories[-10:]),  # last 10 shared memories
            "gossip": eng.gossip.recall(sim_id, other.sim_id) or "",
        }

    # ── Skills with certificates ──────────────────────────────────────────────
    certs = eng.skill_classes.certificates_for(sim_id)
    skills_detail = {
        skill: {"level": lvl, "certificates": certs.get(skill, [])}
        for skill, lvl in sim.skills.levels.items()
    }

    # ── Social activity stats ─────────────────────────────────────────────────
    action_history = dict(getattr(sim, "action_history", {}))
    top_actions = sorted(action_history.items(), key=lambda x: -x[1])[:10]

    # ── Arc / mental states ───────────────────────────────────────────────────
    arc_states = {
        "grief_stage": sim.grief_stage,
        "grief_target": sim.grief_target,
        "grief_tick_count": sim._grief_tick_count,
        "social_drought_ticks": sim._social_drought_ticks,
        "burnout_active": sim._burnout_active,
        "burnout_recovery_ticks": sim._burnout_recovery_ticks,
        "trauma_events": list(sim.trauma_events),
    }

    # ── Moodlets ──────────────────────────────────────────────────────────────
    moodlet_detail = sim.moodlets.active() if hasattr(sim, "moodlets") else []

    # ── Career ────────────────────────────────────────────────────────────────
    career = eng.career_manager.career_summary(sim)

    # ── Traits ────────────────────────────────────────────────────────────────
    traits = {
        "profile_traits": list(sim.profile.get("traits", [])),
        "active": list(active_traits(sim)),
        "reward": sorted(sim.reward_traits),
        "temporary": sorted(sim.temporary_traits),
        "formative": sorted(sim.formative_traits),
        "death": sorted(sim.death_traits),
        "hidden": sorted(sim.hidden_traits),
    }

    # ── Needs with labels ─────────────────────────────────────────────────────
    needs_raw = {
        n: round(getattr(sim.needs, n), 1)
        for n in [
            "hunger",
            "energy",
            "social",
            "fun",
            "hygiene",
            "environment",
            "bladder",
            "comfort",
        ]
    }
    needs_detail = {
        n: {"value": v, "critical": v < 20, "low": v < 40} for n, v in needs_raw.items()
    }

    # ── Lifetime wish ─────────────────────────────────────────────────────────
    lifetime_wish = None
    if hasattr(sim, "lifetime_wish"):
        lw = sim.lifetime_wish
        lifetime_wish = {
            "description": lw.description,
            "fulfilled": lw.fulfilled,
            "progress": round(lw._progress_cache, 3),
        }

    return {
        # ── Identity ──────────────────────────────────────────────────────────
        "id": sim.sim_id,
        "name": sim.name,
        "age": sim.profile.get("age"),
        "job": sim.profile.get("job"),
        "household_id": sim.household_id,
        "married_to": getattr(sim, "_married_to", None),
        "parent_ids": sim.parent_ids,
        "is_ghost": sim.is_ghost,
        "occult_type": sim.occult_type,
        "occult_power": round(sim.occult_power, 1),
        # ── Personality ───────────────────────────────────────────────────────
        "ocean": sim.profile.get("ocean", {}),
        "mbti": sim.profile.get("mbti"),
        "mbti_descriptor": sim.profile.get("mbti_descriptor"),
        "zodiac": sim.profile.get("zodiac", {}).get("sign")
        if isinstance(sim.profile.get("zodiac"), dict)
        else sim.profile.get("zodiac"),
        "aspiration": sim.profile.get("aspiration"),
        "traits": traits,
        "social_orientation": sim.social_orientation,
        "autonomy_profile": sim.autonomy_profile,
        "preferences": sim.preferences,
        "turn_ons": sim.profile.get("turn_ons", []),
        "turn_offs": sim.profile.get("turn_offs", []),
        # ── Finances & status ─────────────────────────────────────────────────
        "simoleons": round(sim.simoleons, 2),
        "reputation_score": round(sim.reputation_score, 1),
        "ei_reputation": round(sim.ei_reputation, 1),
        "celebrity_score": round(getattr(sim, "celebrity_score", 0.0), 1),
        "celebrity_tier": getattr(sim, "celebrity_tier", "none"),
        # ── Health ────────────────────────────────────────────────────────────
        "health_status": sim.health_status,
        "temperature_risk": round(sim.temperature_risk, 2),
        "thermal_state": getattr(sim, "thermal_state", "comfortable"),
        "wellness_state": eng.wellness.state_for(sim_id),
        # ── Needs ─────────────────────────────────────────────────────────────
        "needs": needs_detail,
        # ── Emotional state ───────────────────────────────────────────────────
        "emotion": {
            "dominant": sim.emotion.dominant,
            "valence": sim.emotion.dominant_valence,
            "ts4_emotion": sim.moodlets.ts4_emotion()
            if hasattr(sim, "moodlets")
            else "Fine",
            "ts4_intensity": sim.moodlets.ts4_intensity()
            if hasattr(sim, "moodlets")
            else 0,
            "ts4_color": sim.moodlets.ts4_color()
            if hasattr(sim, "moodlets")
            else "#e9e9e9",
        },
        "moodlets": moodlet_detail,
        "fears": [
            {"label": f.label, "severity": round(f.severity, 2)} for f in sim.fears
        ],
        "active_wants": [
            {
                "description": w.description,
                "priority": w.priority,
                "target": w.target_sim,
            }
            for w in sim.active_wants
        ],
        # ── Career & education ────────────────────────────────────────────────
        "career": career,
        "career_performance": round(sim.career_performance, 1),
        "university_status": sim.university_status,
        "degree_track": sim.degree_track,
        "degree_progress": round(sim.degree_progress, 1),
        "school_performance": round(sim.school_performance, 1),
        # ── Skills ────────────────────────────────────────────────────────────
        "skills": skills_detail,
        "programming_projects": eng.programming.project_state(sim_id),
        "hacker_reputation": round(getattr(sim, "hacker_reputation", 0.0), 2),
        # ── Goals & aspirations ───────────────────────────────────────────────
        "lifetime_wish": lifetime_wish,
        "milestones": list(getattr(sim, "milestones", [])),
        # ── Social activity ───────────────────────────────────────────────────
        "total_interactions": total_interactions,
        "top_actions": [{"action": a, "count": c} for a, c in top_actions],
        "club_ids": list(sim.club_ids),
        "coworker_ids": list(sim.coworker_ids),
        "social_drought_ticks": sim._social_drought_ticks,
        # ── Arc / mental states ───────────────────────────────────────────────
        "arc_states": arc_states,
        # ── Relationships ─────────────────────────────────────────────────────
        "relationships": relationships,
        # ── Inventory & assets ────────────────────────────────────────────────
        "inventory": _build_inventory_profile(sim, eng),
        "properties": list(sim.properties),
        "owned_businesses": list(sim.owned_businesses),
        "property_value": round(eng.properties.total_portfolio_value(sim_id), 2),
        "perks": sorted(sim.perks),
        "perk_points": sim.perk_points,
        "last_dream": getattr(sim, "_last_dream", None),
        "travel_history": list(sim.travel_history),
        "pet_ids": list(sim.pet_ids),
        "home_layout": eng.lot_layout.layout(sim.household_id)
        if sim.household_id
        else None,
        "home_ambiance": eng.lot_layout.ambiance(sim.household_id)
        if sim.household_id
        else None,
    }


@app.post("/interact")
def force_interact(body: dict):
    """Force a specific interaction. Body: {sim_a_id, sim_b_id, action}"""
    eng = _get_engine()
    sim_a = next((s for s in eng.sims if s.sim_id == body.get("sim_a_id")), None)
    sim_b = next((s for s in eng.sims if s.sim_id == body.get("sim_b_id")), None)
    if sim_a is None or sim_b is None:
        raise HTTPException(status_code=404, detail="One or both sims not found")
    action = body.get("action") or choose_interaction(
        sim_a, sim_b, eng.relationships.get(sim_a.sim_id, sim_b.sim_id)
    )
    with _engine_lock:
        eng._submit_interaction(sim_a, sim_b, action, eng._venue)
    return {"queued": True, "action": action}


@app.delete("/reset")
def reset(num_sims: int = 3):
    global _engine
    with _engine_lock:
        if _engine:
            _engine.shutdown()
        _engine = _build_engine(num_sims)
    return {"reset": True, "sims": num_sims}


@app.post("/items/buy")
def buy_item(body: dict):
    """Body: {sim_id, lot_id, object_id, qty?}"""
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    lot_id = str(body.get("lot_id", ""))
    object_id = body.get("object_id")
    qty = int(body.get("qty", 1))
    if not sim_id or not lot_id or object_id is None:
        raise HTTPException(
            status_code=400, detail="sim_id, lot_id, object_id required"
        )
    with _engine_lock:
        result = eng.buy_item(
            sim_id=sim_id, lot_id=lot_id, object_id=int(object_id), qty=qty
        )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "buy_failed"))
    return result


@app.post("/items/sell")
def sell_item(body: dict):
    """Body: {sim_id, object_id, qty?}"""
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    object_id = body.get("object_id")
    qty = int(body.get("qty", 1))
    if not sim_id or object_id is None:
        raise HTTPException(status_code=400, detail="sim_id, object_id required")
    with _engine_lock:
        result = eng.sell_item(sim_id=sim_id, object_id=int(object_id), qty=qty)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "sell_failed"))
    return result


@app.post("/items/gift")
def gift_item(body: dict):
    """Body: {giver_id, receiver_id, object_id?}"""
    eng = _get_engine()
    giver_id = str(body.get("giver_id", ""))
    receiver_id = str(body.get("receiver_id", ""))
    object_id = body.get("object_id")
    if not giver_id or not receiver_id:
        raise HTTPException(status_code=400, detail="giver_id, receiver_id required")
    with _engine_lock:
        result = eng.gift_item(
            giver_id=giver_id,
            receiver_id=receiver_id,
            object_id=int(object_id) if object_id is not None else None,
        )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "gift_failed"))
    return result


@app.post("/items/use")
def use_item(body: dict):
    """Body: {sim_id, object_id}"""
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    object_id = body.get("object_id")
    if not sim_id or object_id is None:
        raise HTTPException(status_code=400, detail="sim_id, object_id required")
    with _engine_lock:
        result = eng.use_item(sim_id=sim_id, object_id=int(object_id))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "use_failed"))
    return result


@app.post("/items/trade")
def trade_item(body: dict):
    """Body: {from_sim_id, to_sim_id, object_id, qty?, unit_price?}"""
    eng = _get_engine()
    from_sim_id = str(body.get("from_sim_id", ""))
    to_sim_id = str(body.get("to_sim_id", ""))
    object_id = body.get("object_id")
    qty = int(body.get("qty", 1))
    unit_price = body.get("unit_price")
    if not from_sim_id or not to_sim_id or object_id is None:
        raise HTTPException(
            status_code=400,
            detail="from_sim_id, to_sim_id, object_id required",
        )
    with _engine_lock:
        result = eng.trade_item(
            from_sim_id=from_sim_id,
            to_sim_id=to_sim_id,
            object_id=int(object_id),
            qty=qty,
            unit_price=float(unit_price) if unit_price is not None else None,
        )
    if not result.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=result.get("reason", "trade_failed"),
        )
    return result


@app.post("/dynasty/create")
def create_dynasty(body: dict):
    """Body: {creator_id, name, description?, crest?, member_ids?, ideals?, focus_skills?}"""
    eng = _get_engine()
    creator_id = str(body.get("creator_id", ""))
    name = str(body.get("name", "")).strip()
    if not creator_id or not name:
        raise HTTPException(status_code=400, detail="creator_id and name required")
    creator = next((s for s in eng.sims if s.sim_id == creator_id), None)
    if creator is None:
        raise HTTPException(status_code=404, detail="creator not found")
    with _engine_lock:
        d = eng.dynasties.create_dynasty(
            creator_id=creator_id,
            name=name,
            description=str(body.get("description", "")),
            crest=body.get("crest") if isinstance(body.get("crest"), dict) else {},
            member_ids=list(body.get("member_ids", []))
            if isinstance(body.get("member_ids", []), list)
            else [],
            ideals=list(body.get("ideals", []))
            if isinstance(body.get("ideals", []), list)
            else [],
            focus_skills=list(body.get("focus_skills", []))
            if isinstance(body.get("focus_skills", []), list)
            else [],
        )
        for sid in d.member_ids:
            sim = next((s for s in eng.sims if s.sim_id == sid), None)
            if sim:
                eng.dynasties.assign_sim(sim, d.dynasty_id)
    return {"ok": True, "dynasty": d.state()}


@app.post("/dynasty/heir")
def set_dynasty_heir(body: dict):
    eng = _get_engine()
    dynasty_id = str(body.get("dynasty_id", ""))
    heir_id = str(body.get("heir_id", ""))
    if not dynasty_id or not heir_id:
        raise HTTPException(status_code=400, detail="dynasty_id and heir_id required")
    with _engine_lock:
        ok = eng.dynasties.set_heir(dynasty_id, heir_id)
    if not ok:
        raise HTTPException(status_code=400, detail="set_heir_failed")
    return {"ok": True}


@app.post("/dynasty/outcast")
def outcast_dynasty_member(body: dict):
    eng = _get_engine()
    dynasty_id = str(body.get("dynasty_id", ""))
    sim_id = str(body.get("sim_id", ""))
    if not dynasty_id or not sim_id:
        raise HTTPException(status_code=400, detail="dynasty_id and sim_id required")
    with _engine_lock:
        ok = eng.dynasties.mark_outcast(dynasty_id, sim_id)
        if ok:
            sim = next((s for s in eng.sims if s.sim_id == sim_id), None)
            if sim:
                eng.dynasties.assign_sim(sim, None)
    if not ok:
        raise HTTPException(status_code=400, detail="outcast_failed")
    return {"ok": True}


@app.post("/dynasty/perk")
def spend_dynasty_perk(body: dict):
    eng = _get_engine()
    dynasty_id = str(body.get("dynasty_id", ""))
    perk_id = str(body.get("perk_id", ""))
    if not dynasty_id or not perk_id:
        raise HTTPException(status_code=400, detail="dynasty_id and perk_id required")
    with _engine_lock:
        ok = eng.dynasties.spend_perk_points(dynasty_id, perk_id)
    if not ok:
        raise HTTPException(status_code=400, detail="perk_purchase_failed")
    return {"ok": True}


@app.post("/dynasty/alliance")
def set_dynasty_alliance(body: dict):
    eng = _get_engine()
    dynasty_id = str(body.get("dynasty_id", ""))
    other_dynasty_id = str(body.get("other_dynasty_id", ""))
    if not dynasty_id or not other_dynasty_id:
        raise HTTPException(
            status_code=400,
            detail="dynasty_id and other_dynasty_id required",
        )
    with _engine_lock:
        ok = eng.dynasties.add_alliance(dynasty_id, other_dynasty_id)
    if not ok:
        raise HTTPException(status_code=400, detail="alliance_failed")
    return {"ok": True}


@app.post("/dynasty/rivalry")
def set_dynasty_rivalry(body: dict):
    eng = _get_engine()
    dynasty_id = str(body.get("dynasty_id", ""))
    other_dynasty_id = str(body.get("other_dynasty_id", ""))
    if not dynasty_id or not other_dynasty_id:
        raise HTTPException(
            status_code=400,
            detail="dynasty_id and other_dynasty_id required",
        )
    with _engine_lock:
        ok = eng.dynasties.add_rivalry(dynasty_id, other_dynasty_id)
    if not ok:
        raise HTTPException(status_code=400, detail="rivalry_failed")
    return {"ok": True}


@app.get("/items/lot/{lot_id}")
def get_lot_items(lot_id: str):
    eng = _get_engine()
    return {
        "lot_id": lot_id,
        "items": eng.objects.lot_stock_state(lot_id),
    }


# ── Home lot layout endpoints ─────────────────────────────────────────────────


@app.get("/lot/{lot_id}/layout")
def get_lot_layout(lot_id: str):
    """Full room-by-room placement map with per-item passive effects."""
    eng = _get_engine()
    return eng.lot_layout.layout(lot_id)


@app.get("/lot/{lot_id}/ambiance")
def get_lot_ambiance(lot_id: str):
    """Aggregated passive need bonuses the lot provides per tick."""
    eng = _get_engine()
    return eng.lot_layout.ambiance(lot_id)


@app.post("/lot/{lot_id}/place")
def place_object(lot_id: str, body: dict):
    """
    Move an item from a sim's inventory into a home zone.
    Body: {sim_id, object_id, zone}  (zone defaults to best-fit if omitted)
    """
    from world.lot_layout import ZONES, ZONE_ALLOWED_TYPES

    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    object_id = body.get("object_id")
    zone = str(body.get("zone", ""))

    if not sim_id or object_id is None:
        raise HTTPException(status_code=400, detail="sim_id and object_id required")

    sim = next((s for s in eng.sims if s.sim_id == sim_id), None)
    if sim is None:
        raise HTTPException(status_code=404, detail=f"Sim {sim_id} not found")

    # Verify the sim lives in this lot
    if sim.household_id != lot_id:
        raise HTTPException(status_code=403, detail="Sim does not live in this lot")

    # Find item in sim's inventory
    inv = list(getattr(sim, "inventory_objects", []))
    item = next((o for o in inv if int(o.get("id", -1)) == int(object_id)), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found in sim inventory")

    # Auto-pick zone if not specified
    if not zone:
        item_type = str(item.get("type", "Other"))
        zone = _best_zone_for_type(item_type, eng.lot_layout, lot_id)

    with _engine_lock:
        result = eng.lot_layout.place(lot_id, zone, item)
        if result["ok"]:
            # Remove from sim inventory
            sim.inventory_objects = [
                o for o in inv if not (int(o.get("id", -1)) == int(object_id))
            ]
            sim.inventory = [o["name"] for o in sim.inventory_objects]

    if not result["ok"]:
        raise HTTPException(
            status_code=400, detail=result.get("reason", "place_failed")
        )
    return {**result, "zone": zone, "item_name": item.get("name"), "lot_id": lot_id}


@app.post("/lot/{lot_id}/remove")
def remove_object(lot_id: str, body: dict):
    """
    Return a placed item back to the sim's inventory.
    Body: {sim_id, object_id}
    """
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    object_id = body.get("object_id")

    if not sim_id or object_id is None:
        raise HTTPException(status_code=400, detail="sim_id and object_id required")

    sim = next((s for s in eng.sims if s.sim_id == sim_id), None)
    if sim is None:
        raise HTTPException(status_code=404, detail=f"Sim {sim_id} not found")

    if sim.household_id != lot_id:
        raise HTTPException(status_code=403, detail="Sim does not live in this lot")

    with _engine_lock:
        result = eng.lot_layout.remove(lot_id, int(object_id))
        if result["ok"]:
            inv = list(getattr(sim, "inventory_objects", []))
            inv_constrained = eng.objects._apply_inventory_constraints(
                sim, inv + [result["item"]]
            )
            sim.inventory_objects = inv_constrained
            sim.inventory = [o["name"] for o in sim.inventory_objects]

    if not result["ok"]:
        raise HTTPException(
            status_code=400, detail=result.get("reason", "remove_failed")
        )
    return {**result, "lot_id": lot_id}


def _best_zone_for_type(item_type: str, layout, lot_id: str) -> str:
    """Pick the first zone that accepts this item type and has space."""
    from world.lot_layout import ZONES, ZONE_ALLOWED_TYPES, ZONE_CAPACITY, _OPEN_TYPES

    lot = layout._lot(lot_id)
    for zone in ZONES:
        allowed = ZONE_ALLOWED_TYPES.get(zone, set()) | _OPEN_TYPES
        if item_type in allowed or item_type in _OPEN_TYPES:
            if len(lot.get(zone, [])) < ZONE_CAPACITY:
                return zone
    return "living_room"  # fallback


# ── Grim Reaper endpoints ─────────────────────────────────────────────────────


@app.get("/grim/status")
def grim_status():
    """Current Grim Reaper state — presence, lot, linger timer, tombstones."""
    return _get_engine().grim_reaper.state()


@app.post("/grim/plead")
def grim_plead(body: dict):
    """
    Attempt to plead with Grim to spare a dying Sim.
    Body: {sim_id, target_sim_id}
    """
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    target_id = str(body.get("target_sim_id", ""))
    if not sim_id or not target_id:
        raise HTTPException(status_code=400, detail="sim_id and target_sim_id required")
    pleader = next((s for s in eng.sims if s.sim_id == sim_id), None)
    target = next((s for s in eng.sims if s.sim_id == target_id), None)
    if not pleader or not target:
        raise HTTPException(status_code=404, detail="One or both sims not found")
    with _engine_lock:
        result = eng.grim_reaper.attempt_plead(pleader, target)
    return result


@app.post("/grim/chess")
def grim_chess(body: dict):
    """
    Challenge Grim to chess to resurrect a recently dead Sim.
    Body: {sim_id}
    """
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    if not sim_id:
        raise HTTPException(status_code=400, detail="sim_id required")
    sim = next((s for s in eng.sims if s.sim_id == sim_id), None)
    if not sim:
        raise HTTPException(status_code=404, detail=f"Sim {sim_id} not found")
    with _engine_lock:
        result = eng.grim_reaper.attempt_chess(sim)
    return result


@app.post("/grim/pet_save")
def grim_pet_save(body: dict):
    """
    Have a pet harass Grim to save their master.
    Body: {pet_sim_id, dying_sim_id}
    """
    eng = _get_engine()
    pet_id = str(body.get("pet_sim_id", ""))
    dying_id = str(body.get("dying_sim_id", ""))
    if not pet_id or not dying_id:
        raise HTTPException(
            status_code=400, detail="pet_sim_id and dying_sim_id required"
        )
    pet = next((s for s in eng.sims if s.sim_id == pet_id), None)
    dying = next((s for s in eng.sims if s.sim_id == dying_id), None)
    if not pet or not dying:
        raise HTTPException(status_code=404, detail="One or both sims not found")
    with _engine_lock:
        result = eng.grim_reaper.pet_save_attempt(pet, dying)
    return result


@app.get("/grim/tombstones")
def grim_tombstones(lot_id: str | None = None):
    """List all tombstones, optionally filtered by lot_id."""
    eng = _get_engine()
    stones = eng.grim_reaper.tombstones
    if lot_id:
        stones = [s for s in stones if s.get("lot_id") == lot_id]
    return {"tombstones": stones, "count": len(stones)}


@app.get("/burglar/status")
def burglar_status():
    eng = _get_engine()
    return eng.burglar.state()


@app.get("/burglar/log")
def burglar_log(limit: int = 25):
    eng = _get_engine()
    st = eng.burglar.state()
    events = list(st.get("recent_events", []))[-max(1, min(int(limit), 200)) :]
    return {"count": len(events), "events": events}


@app.post("/burglar/trigger")
def burglar_trigger(body: dict):
    """Body: {lot_id?}"""
    eng = _get_engine()
    lot_id = body.get("lot_id")
    lot_val = str(lot_id) if lot_id is not None else None
    with _engine_lock:
        result = eng.burglar.force_trigger(eng, lot_id=lot_val)
    if not result.get("ok"):
        raise HTTPException(
            status_code=400, detail=result.get("reason", "trigger_failed")
        )
    return result


@app.get("/investments/{sim_id}")
def investment_dashboard(sim_id: str):
    eng = _get_engine()
    sim = next((s for s in eng.sims if s.sim_id == sim_id), None)
    if sim is None:
        raise HTTPException(status_code=404, detail=f"Sim {sim_id} not found")
    return eng.properties.investment_dashboard(sim_id)


@app.post("/investments/buy")
def investment_buy(body: dict):
    """Body: {sim_id, venue_type, ownership_state?, district?}"""
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    venue_type = str(body.get("venue_type", "")).strip()
    ownership_state = str(body.get("ownership_state", "partner")).strip()
    district = str(body.get("district", "central")).strip()
    if not sim_id or not venue_type:
        raise HTTPException(status_code=400, detail="sim_id and venue_type required")
    with _engine_lock:
        result = eng.property_purchase(
            sim_id=sim_id,
            venue_type=venue_type,
            ownership_state=ownership_state,
            district=district,
        )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "buy_failed"))
    return result


@app.post("/investments/collect")
def investment_collect(body: dict):
    """Body: {sim_id, property_id}"""
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    property_id = str(body.get("property_id", ""))
    if not sim_id or not property_id:
        raise HTTPException(status_code=400, detail="sim_id and property_id required")
    with _engine_lock:
        result = eng.property_collect_income(sim_id, property_id)
    if not result.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=result.get("reason", "collect_failed"),
        )
    return result


@app.post("/investments/upgrade")
def investment_upgrade(body: dict):
    """Body: {sim_id, property_id}"""
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    property_id = str(body.get("property_id", ""))
    if not sim_id or not property_id:
        raise HTTPException(status_code=400, detail="sim_id and property_id required")
    with _engine_lock:
        result = eng.property_upgrade(sim_id, property_id)
    if not result.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=result.get("reason", "upgrade_failed"),
        )
    return result


@app.post("/investments/rename")
def investment_rename(body: dict):
    """Body: {sim_id, property_id, new_name}"""
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    property_id = str(body.get("property_id", ""))
    new_name = str(body.get("new_name", ""))
    if not sim_id or not property_id or not new_name.strip():
        raise HTTPException(
            status_code=400,
            detail="sim_id, property_id, new_name required",
        )
    with _engine_lock:
        result = eng.properties.rename_business(sim_id, property_id, new_name)
    if not result.get("ok"):
        raise HTTPException(
            status_code=400, detail=result.get("reason", "rename_failed")
        )
    return result


@app.post("/investments/employee")
def investment_employee(body: dict):
    """Body: {sim_id, property_id, action, employee_id?}"""
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    property_id = str(body.get("property_id", ""))
    action = str(body.get("action", "")).strip()
    employee_id = str(body.get("employee_id", ""))
    if not sim_id or not property_id or action not in {"hire", "fire"}:
        raise HTTPException(
            status_code=400,
            detail="sim_id, property_id, action(hire|fire) required",
        )
    with _engine_lock:
        result = eng.property_manage_employee(sim_id, property_id, action, employee_id)
    if not result.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=result.get("reason", "employee_action_failed"),
        )
    return result


@app.post("/investments/sell")
def investment_sell(body: dict):
    """Body: {sim_id, property_id}"""
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    property_id = str(body.get("property_id", ""))
    if not sim_id or not property_id:
        raise HTTPException(status_code=400, detail="sim_id and property_id required")
    with _engine_lock:
        result = eng.property_sell(sim_id, property_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "sell_failed"))
    return result


@app.get("/ledger")
def ledger_state():
    eng = _get_engine()
    return {
        "state": eng.ledger.state(),
        "recent_txs": eng.ledger.recent_txs(40),
    }


@app.get("/contracts")
def contracts_state(active_only: bool = False):
    eng = _get_engine()
    return {
        "stats": eng.contracts_engine.stats(),
        "contracts": eng.contracts_engine.list_contracts(active_only=bool(active_only)),
    }


@app.post("/contracts/loan")
def contracts_loan(body: dict):
    eng = _get_engine()
    lender_id = str(body.get("lender_id", ""))
    borrower_id = str(body.get("borrower_id", ""))
    principal = float(body.get("principal", 0.0))
    interest_rate = float(body.get("interest_rate", 0.05))
    duration_ticks = int(body.get("duration_ticks", 40))
    if not lender_id or not borrower_id or principal <= 0:
        raise HTTPException(
            status_code=400, detail="lender_id, borrower_id, principal required"
        )
    with _engine_lock:
        out = eng.create_contract_loan(
            lender_id, borrower_id, principal, interest_rate, duration_ticks
        )
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail="loan_create_failed")
    return out


@app.post("/contracts/employment")
def contracts_employment(body: dict):
    eng = _get_engine()
    employer_id = str(body.get("employer_id", ""))
    employee_id = str(body.get("employee_id", ""))
    wage = float(body.get("wage", 0.0))
    period_ticks = int(body.get("period_ticks", 5))
    severance = float(body.get("severance", 50.0))
    if not employer_id or not employee_id or wage <= 0:
        raise HTTPException(
            status_code=400, detail="employer_id, employee_id, wage required"
        )
    with _engine_lock:
        out = eng.create_contract_employment(
            employer_id, employee_id, wage, period_ticks, severance
        )
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail="employment_create_failed")
    return out


@app.post("/contracts/partnership")
def contracts_partnership(body: dict):
    eng = _get_engine()
    a_id = str(body.get("a_id", ""))
    b_id = str(body.get("b_id", ""))
    revenue_share = float(body.get("revenue_share", 0.2))
    buyout = float(body.get("buyout", 10000.0))
    if not a_id or not b_id:
        raise HTTPException(status_code=400, detail="a_id and b_id required")
    with _engine_lock:
        out = eng.create_contract_partnership(a_id, b_id, revenue_share, buyout)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail="partnership_create_failed")
    return out


@app.get("/stocks")
def stocks_state():
    eng = _get_engine()
    return eng.stocks.state()


@app.post("/stocks/buy")
def stocks_buy(body: dict):
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    ticker = str(body.get("ticker", "")).upper()
    shares = int(body.get("shares", 0))
    if not sim_id or not ticker or shares <= 0:
        raise HTTPException(status_code=400, detail="sim_id, ticker, shares required")
    with _engine_lock:
        out = eng.stock_buy(sim_id, ticker, shares)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail="stock_buy_failed")
    return out


@app.post("/stocks/sell")
def stocks_sell(body: dict):
    eng = _get_engine()
    sim_id = str(body.get("sim_id", ""))
    ticker = str(body.get("ticker", "")).upper()
    shares = int(body.get("shares", 0))
    if not sim_id or not ticker or shares <= 0:
        raise HTTPException(status_code=400, detail="sim_id, ticker, shares required")
    with _engine_lock:
        out = eng.stock_sell(sim_id, ticker, shares)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail="stock_sell_failed")
    return out


@app.post("/economy/gift")
def economy_gift(body: dict):
    eng = _get_engine()
    from_sim_id = str(body.get("from_sim_id", "")).strip()
    to_sim_id = str(body.get("to_sim_id", "")).strip()
    amount = float(body.get("amount", 0.0))
    channel = str(body.get("channel", "direct")).strip() or "direct"
    if not from_sim_id or not to_sim_id or amount <= 0:
        raise HTTPException(
            status_code=400,
            detail="from_sim_id, to_sim_id, amount > 0 required",
        )
    with _engine_lock:
        out = eng.gift_money(from_sim_id, to_sim_id, amount, channel=channel)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("reason", "gift_failed"))
    return out


@app.get("/tokens/wallet/{sim_id}")
def token_wallet(sim_id: str):
    eng = _get_engine()
    out = eng.token_wallet(sim_id)
    if not out.get("ok"):
        raise HTTPException(status_code=404, detail=out.get("reason", "sim_not_found"))
    return out


@app.get("/tokens/market")
def token_market():
    eng = _get_engine()
    return eng.token_marketplace()


@app.post("/tokens/market/list")
def token_market_list(body: dict):
    eng = _get_engine()
    owner_id = str(body.get("owner_id", "")).strip()
    token_id = str(body.get("token_id", "")).strip()
    price = float(body.get("price_simcoin", 0.0))
    if not owner_id or not token_id or price <= 0:
        raise HTTPException(
            status_code=400, detail="owner_id, token_id, price_simcoin required"
        )
    with _engine_lock:
        out = eng.token_market_list(owner_id, token_id, price)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail="token_list_failed")
    return out


@app.post("/tokens/market/cancel")
def token_market_cancel(body: dict):
    eng = _get_engine()
    owner_id = str(body.get("owner_id", "")).strip()
    token_id = str(body.get("token_id", "")).strip()
    if not owner_id or not token_id:
        raise HTTPException(status_code=400, detail="owner_id and token_id required")
    with _engine_lock:
        out = eng.token_market_cancel(owner_id, token_id)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail="token_cancel_failed")
    return out


@app.post("/tokens/market/buy")
def token_market_buy(body: dict):
    eng = _get_engine()
    buyer_id = str(body.get("buyer_id", "")).strip()
    token_id = str(body.get("token_id", "")).strip()
    if not buyer_id or not token_id:
        raise HTTPException(status_code=400, detail="buyer_id and token_id required")
    with _engine_lock:
        out = eng.token_market_buy(buyer_id, token_id)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail="token_buy_failed")
    return out


@app.post("/wallet/nonce")
def wallet_nonce(body: dict):
    """
    SIWE challenge for MetaMask linking.
    Requires an auth token — players can only generate challenges for their own sim.

    Body: {token, address}   (address = MetaMask 0x address the player will sign with)
    """
    token   = str(body.get("token", "")).strip()
    address = str(body.get("address", "")).strip()

    user = _require_auth(token)   # 401 if invalid
    if not address or not address.startswith("0x"):
        raise HTTPException(status_code=400, detail="address (0x…) required")

    eng = _get_engine()
    with _engine_lock:
        out = eng.wallet_nonce(user.sim_id, address=address)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("reason", "nonce_failed"))
    return out


@app.post("/wallet/link")
def wallet_link(body: dict):
    """
    Verify a MetaMask personal_sign and record the MetaMask address as the
    player's identity for their sim.

    The game wallet (deterministic, server-managed) is NOT replaced.
    MetaMask becomes the authentication and consent layer only.

    Body: {token, wallet_address, signature, message, nonce}
    - token          auth token from /auth/login or /auth/signup
    - wallet_address the MetaMask 0x address that signed the message
    - signature      from MetaMask personal_sign
    - message        exact string returned by /wallet/nonce
    - nonce          nonce returned by /wallet/nonce
    """
    token          = str(body.get("token", "")).strip()
    wallet_address = str(body.get("wallet_address", "")).strip()
    signature      = str(body.get("signature", "")).strip()
    message        = str(body.get("message", "")).strip()
    nonce          = str(body.get("nonce", "")).strip()

    user = _require_auth(token)   # 401 if invalid

    if not wallet_address or not signature:
        raise HTTPException(
            status_code=400,
            detail="wallet_address and signature required",
        )

    eng  = _get_engine()
    auth = _get_auth()
    with _engine_lock:
        out = eng.wallet_link(
            user.sim_id,           # always the authenticated user's own sim
            wallet_address,
            signature,
            message=message,
            nonce=nonce,
            auth_user_id=user.user_id,
            auth_store=auth,
        )
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out)
    return out


# ── Ethereum JSON-RPC endpoint (MetaMask connects here) ───────────────────────

@app.post("/chain/rpc")
async def chain_rpc(body: dict):
    """
    Ethereum JSON-RPC 2.0 endpoint.

    Add SimChain to MetaMask with:
      wallet_addEthereumChain({
        chainId: "0x3433",
        chainName: "SimChain",
        rpcUrls: ["http://<host>:<port>/chain/rpc"],
        nativeCurrency: { name: "SimCoin", symbol: "SIM", decimals: 18 }
      })
    """
    eng = _get_engine()
    from blockchain.rpc import dispatch
    return dispatch(body, eng.chain)


@app.get("/chain/connect")
def chain_connect(host: str = "localhost", port: int = 8080):
    """
    Return the wallet_addEthereumChain params for MetaMask.

    Frontend usage:
      const params = await fetch('/chain/connect').then(r => r.json());
      await window.ethereum.request({ method: 'wallet_addEthereumChain', params: [params] });
    """
    from blockchain.eip712 import metamask_add_chain_params
    rpc_url = f"http://{host}:{port}/chain/rpc"
    return metamask_add_chain_params(rpc_url)


@app.get("/chain/status")
def chain_status():
    """SimChain health — height, validator, pending txs, deployed contracts."""
    eng = _get_engine()
    return {**eng.chain.summary(), "node": eng.chain_node.stats()}


@app.post("/chain/challenge")
def chain_challenge(body: dict):
    """
    Standalone SIWE challenge (address-first flow, no sim_id needed yet).
    Use this when the player connects MetaMask before choosing a sim.
    """
    address = str(body.get("address", "")).strip()
    if not address or not address.startswith("0x"):
        raise HTTPException(status_code=400, detail="valid 0x address required")
    from blockchain.siwe import create_challenge
    return create_challenge(address, domain="simchain.game")


@app.post("/chain/verify")
def chain_verify(body: dict):
    """
    Verify a SIWE signature and record the MetaMask address as the player's identity.
    Requires an auth token — players can only link their own sim.

    Body: {token, address, signature, message, nonce}
    """
    token     = str(body.get("token", "")).strip()
    address   = str(body.get("address", "")).strip()
    signature = str(body.get("signature", "")).strip()
    message   = str(body.get("message", "")).strip()
    nonce     = str(body.get("nonce", "")).strip()

    if not all([address, signature, message, nonce]):
        raise HTTPException(
            status_code=400,
            detail="address, signature, message, nonce required",
        )

    try:
        from blockchain.siwe import verify_challenge
        recovered = verify_challenge(nonce, address, signature, message)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    eng  = _get_engine()
    auth = _get_auth()
    result: dict = {"ok": True, "address": recovered}

    if token:
        # Auth mode: link to the authenticated user's own sim
        user = _require_auth(token)
        with _engine_lock:
            link_out = eng.wallet_link(
                user.sim_id, recovered, signature,
                message=message, nonce=nonce,
                auth_user_id=user.user_id, auth_store=auth,
            )
        result.update(link_out)
    else:
        # Unauthenticated lookup only — no linking
        linked_sim = eng.web3.sim_id_for_metamask(recovered)
        if linked_sim:
            wi = eng.web3.wallet_info(linked_sim)
            result["sim_id"]    = linked_sim
            result["wallet"]    = wi
        else:
            result["sim_id"]    = None
            result["message"]   = "wallet verified but not yet linked to a sim — pass token to link"
    return result


@app.post("/wallet/unlink")
def wallet_unlink(body: dict):
    eng = _get_engine()
    sim_id = str(body.get("sim_id", "")).strip()
    if not sim_id:
        raise HTTPException(status_code=400, detail="sim_id required")
    with _engine_lock:
        out = eng.wallet_unlink(sim_id)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("reason", "unlink_failed"))
    return out


@app.get("/wallet/status/{sim_id}")
def wallet_status(sim_id: str):
    eng = _get_engine()
    out = eng.wallet_status(sim_id)
    if not out.get("ok"):
        raise HTTPException(status_code=404, detail=out.get("reason", "status_failed"))
    return out


@app.post("/wallet/mirror")
def wallet_mirror(body: dict):
    eng = _get_engine()
    sim_id = str(body.get("sim_id", "")).strip()
    native_balance = float(body.get("native_balance", 0.0))
    simcoin_erc20 = float(body.get("simcoin_erc20", 0.0))
    nfts = body.get("nfts", [])
    if not sim_id:
        raise HTTPException(status_code=400, detail="sim_id required")
    with _engine_lock:
        out = eng.wallet_set_mirror(sim_id, native_balance, simcoin_erc20, nfts)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("reason", "mirror_failed"))
    return out


@app.get("/chain/intents")
def chain_intents(limit: int = 100):
    eng = _get_engine()
    return eng.chain_intents_view(limit=limit)


@app.post("/bookie/refresh")
def bookie_refresh():
    eng = _get_engine()
    with _engine_lock:
        out = eng.bookie_refresh()
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out)
    return out


@app.get("/bookie/matches")
def bookie_matches():
    eng = _get_engine()
    return eng.bookie_matches()


@app.post("/bookie/player/fund")
def bookie_player_fund(body: dict):
    eng = _get_engine()
    player_id = str(body.get("player_id", "")).strip()
    amount = float(body.get("amount", 0.0))
    if not player_id or amount <= 0:
        raise HTTPException(status_code=400, detail="player_id and amount>0 required")
    with _engine_lock:
        return eng.player_bookie_fund(player_id, amount)


@app.post("/bookie/bet/sim")
def bookie_bet_sim(body: dict):
    eng = _get_engine()
    sim_id = str(body.get("sim_id", "")).strip()
    match_id = str(body.get("match_id", "")).strip()
    selection = str(body.get("selection", "")).strip()
    stake = float(body.get("stake", 0.0))
    if not sim_id or not match_id or not selection or stake <= 0:
        raise HTTPException(
            status_code=400, detail="sim_id, match_id, selection, stake>0 required"
        )
    with _engine_lock:
        out = eng.place_sim_bet(sim_id, match_id, selection, stake)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out)
    return out


@app.post("/bookie/bet/player")
def bookie_bet_player(body: dict):
    eng = _get_engine()
    player_id = str(body.get("player_id", "")).strip()
    match_id = str(body.get("match_id", "")).strip()
    selection = str(body.get("selection", "")).strip()
    stake = float(body.get("stake", 0.0))
    if not player_id or not match_id or not selection or stake <= 0:
        raise HTTPException(
            status_code=400, detail="player_id, match_id, selection, stake>0 required"
        )
    with _engine_lock:
        out = eng.place_player_bet(player_id, match_id, selection, stake)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out)
    return out


@app.get("/economy/overview")
def economy_overview():
    eng = _get_engine()
    return eng.economy_overview()


@app.get("/sim/{sim_id}/portfolio")
def sim_portfolio(sim_id: str):
    eng = _get_engine()
    out = eng.sim_portfolio(sim_id)
    if not out.get("ok"):
        raise HTTPException(status_code=404, detail=out.get("reason", "sim_not_found"))
    return out


@app.post("/stocks/backfill")
def stocks_backfill(body: dict):
    eng = _get_engine()
    api_key = str(body.get("api_key", "")).strip()
    outputsize = str(body.get("outputsize", "compact")).strip() or "compact"
    endpoint_base = str(
        body.get("endpoint_base", "https://www.alphavantage.co/query")
    ).strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key required")
    with _engine_lock:
        out = eng.stocks.backfill_from_alpha_vantage(
            api_key=api_key,
            outputsize=outputsize,
            endpoint_base=endpoint_base,
        )
        eng.ledger.record(
            "stocks_backfill",
            eng.tick_count,
            {"outputsize": outputsize, "ok": out.get("ok")},
        )
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out)
    return out


@app.get("/items/search")
def search_items(
    q: str = "",
    type: str | None = None,
    rarity: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    limit: int = 50,
):
    eng = _get_engine()
    results = eng.objects.search_catalog(
        q=q,
        type_filter=type,
        rarity=rarity,
        min_price=min_price,
        max_price=max_price,
        limit=limit,
    )
    return {
        "query": {
            "q": q,
            "type": type,
            "rarity": rarity,
            "min_price": min_price,
            "max_price": max_price,
            "limit": limit,
        },
        "count": len(results),
        "items": results,
    }


@app.get("/natives")
def list_natives(namespace: str | None = None):
    eng = _get_engine()
    natives = eng.natives.list(namespace=namespace)
    return {
        "namespace": namespace,
        "count": len(natives),
        "natives": natives,
    }


@app.post("/natives/call")
def call_native(body: dict):
    eng = _get_engine()
    native_name = str(body.get("native", "")).strip()
    if not native_name:
        raise HTTPException(status_code=400, detail="native is required")
    kwargs = body.get("args", {})
    if not isinstance(kwargs, dict):
        raise HTTPException(status_code=400, detail="args must be an object")
    with _engine_lock:
        result = eng.natives.call(native_name, **kwargs)
    if isinstance(result, dict) and not result.get("ok", True):
        raise HTTPException(status_code=400, detail=result)
    return {"native": native_name.upper(), "result": result}


# ─── Auth: signup / login / logout / profile ──────────────────────────────────

def _generate_unique_sim_id(engine: SimEngine, auth: "_AuthStore") -> str:
    """
    Generate a collision-free UUID4 sim_id.
    Checks both the live engine lookup and the auth DB (covers sims from
    previous server runs that may have been persisted).
    UUID4 space is 2^122 — collision is astronomically unlikely but we verify
    to be correct by design.
    """
    import uuid as _uuid
    for _ in range(10):
        candidate = _uuid.uuid4().hex
        if candidate not in engine._sim_lookup and not auth.sim_id_taken(candidate):
            return candidate
    raise RuntimeError("Could not generate a unique sim_id after 10 attempts.")


def _build_sim_profile(
    sim_name: str,
    personality: dict,
    appearance_data: dict,
    sim_id: str,
) -> dict:
    """
    Construct a sim profile dict from user-supplied signup data.

    Base profile is generated by generate_sim_profile() (faker identity,
    OCEAN inference, traits, job).  User overrides are applied on top:
      • name           → sim_name
      • ocean          → personality.ocean (partial OK, fills gaps from base)
      • traits         → personality.traits (list, max 5)
      • aspiration     → personality.aspiration
      • mbti           → personality.mbti
      • appearance     → validated SimAppearance dict
    """
    from identity.profile_factory import generate_sim_profile
    from core.appearance import validate_appearance, default_appearance, AppearanceValidationError

    base = generate_sim_profile()
    base["id"]   = sim_id
    base["name"] = sim_name.strip()[:50]

    # OCEAN override — merge; unknown keys are ignored
    _OCEAN_KEYS = ("openness", "conscientiousness", "extraversion",
                   "agreeableness", "neuroticism")
    user_ocean = personality.get("ocean", {})
    for key in _OCEAN_KEYS:
        if key in user_ocean:
            try:
                val = float(user_ocean[key])
                if 0.0 <= val <= 1.0:
                    base.setdefault("ocean", {})[key] = round(val, 3)
            except (TypeError, ValueError):
                pass

    # Traits override (max 5, strings only)
    user_traits = personality.get("traits", [])
    if isinstance(user_traits, list) and user_traits:
        base["traits"] = [str(t).strip()[:40] for t in user_traits[:5] if t]

    # Aspiration
    if personality.get("aspiration"):
        base["aspiration"] = str(personality["aspiration"]).strip()[:60]

    # MBTI
    _VALID_MBTI = {
        "INTJ","INTP","ENTJ","ENTP","INFJ","INFP","ENFJ","ENFP",
        "ISTJ","ISFJ","ESTJ","ESFJ","ISTP","ISFP","ESTP","ESFP",
    }
    mbti = str(personality.get("mbti", "")).strip().upper()
    if mbti in _VALID_MBTI:
        base["mbti"] = mbti

    # Appearance
    try:
        app = validate_appearance(appearance_data)
    except AppearanceValidationError:
        app = default_appearance()
    base["appearance"] = app.to_dict()

    return base


@app.get("/auth/options")
def auth_options():
    """Return allowed values for appearance and personality fields (frontend dropdowns)."""
    from core.appearance import options as _appearance_options
    from config import INTERACTION_TYPES
    traits_sample = list(set(
        t for actions in INTERACTION_TYPES.values() for t in actions
    ))[:30]
    aspirations = [
        "popularity", "romance", "wealth", "knowledge",
        "family", "creativity", "athleticism", "fame",
    ]
    mbti_list = [
        "INTJ","INTP","ENTJ","ENTP","INFJ","INFP","ENFJ","ENFP",
        "ISTJ","ISFJ","ESTJ","ESFJ","ISTP","ISFP","ESTP","ESFP",
    ]
    return {
        "appearance":  _appearance_options(),
        "aspirations": aspirations,
        "mbti":        mbti_list,
        "ocean_range": {"min": 0.0, "max": 1.0},
    }


@app.post("/auth/signup")
def auth_signup(body: dict):
    """
    Register a new player and create their sim.

    Required fields:
      username    str   3–30 chars, alphanumeric + underscores
      email       str   valid email
      password    str   ≥ 8 chars
      sim_name    str   the sim's display name (2–50 chars)

    Optional fields:
      personality dict  {ocean: {openness, …}, traits: [], aspiration, mbti}
      appearance  dict  {skin_tone, hair_color, hair_style, eye_color, build,
                          height, style, accessories: [], bio}

    Returns:
      {ok, token, user_id, sim_id, sim_name, username}
    """
    from persistence.auth import validate_username, validate_email, validate_password, ValueError as _VE
    from core.sim import Sim

    # ── Input extraction ──────────────────────────────────────────────────────
    username    = str(body.get("username", "")).strip()
    email       = str(body.get("email", "")).strip()
    password    = str(body.get("password", ""))
    sim_name    = str(body.get("sim_name", "")).strip()
    personality = body.get("personality", {}) or {}
    appearance  = body.get("appearance", {}) or {}

    if not sim_name or len(sim_name) < 2:
        raise HTTPException(status_code=400, detail="sim_name must be at least 2 characters.")

    # ── Validation ────────────────────────────────────────────────────────────
    try:
        validate_username(username)
        validate_email(email)
        validate_password(password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    auth = _get_auth()
    eng  = _get_engine()

    # ── Generate a guaranteed-unique sim_id ───────────────────────────────────
    sim_id = _generate_unique_sim_id(eng, auth)

    # ── Build sim profile with personality + appearance ───────────────────────
    profile = _build_sim_profile(sim_name, personality, appearance, sim_id)

    # ── Create user record (raises ValueError on duplicate username/email) ────
    try:
        with _engine_lock:
            user = auth.create_user(username, email, password, sim_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # ── Instantiate sim and inject into live engine ────────────────────────────
    sim = Sim(profile)
    with _engine_lock:
        ok = eng.add_sim(sim)
    if not ok:
        # Extremely unlikely (UUID collision); roll back user record
        auth.set_active(user.user_id, False)
        raise HTTPException(status_code=500, detail="Sim ID conflict — please retry.")

    # ── Issue session token ───────────────────────────────────────────────────
    token = auth.create_session(user.user_id)

    # ── Claim sim in online world ─────────────────────────────────────────────
    online_sess = _online_world.create_session(username)
    _online_world.claim_sim(online_sess, sim_id)

    return {
        "ok":       True,
        "token":    token,
        "user_id":  user.user_id,
        "sim_id":   sim_id,
        "sim_name": sim.name,
        "username": user.username,
        "appearance": profile.get("appearance", {}),
        "ocean":    profile.get("ocean", {}),
        "traits":   profile.get("traits", []),
        "mbti":     profile.get("mbti", ""),
    }


@app.post("/auth/login")
def auth_login(body: dict):
    """
    Authenticate an existing player.

    Body: {credential (username or email), password}
    Returns: {ok, token, user_id, sim_id, username}
    """
    credential = str(body.get("credential", body.get("username", body.get("email", "")))).strip()
    password   = str(body.get("password", ""))
    if not credential or not password:
        raise HTTPException(status_code=400, detail="credential and password required.")

    auth = _get_auth()
    user = auth.authenticate(credential, password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid credentials.")

    token = auth.create_session(user.user_id)

    # Sync online world session
    online_sess = _online_world.create_session(user.username)
    if user.sim_id:
        eng = _get_engine()
        if any(s.sim_id == user.sim_id for s in eng.sims):
            _online_world.claim_sim(online_sess, user.sim_id)

    return {
        "ok":       True,
        "token":    token,
        "user_id":  user.user_id,
        "sim_id":   user.sim_id,
        "username": user.username,
    }


@app.post("/auth/logout")
def auth_logout(body: dict):
    """Revoke the bearer token. Body: {token}"""
    token = str(body.get("token", "")).strip()
    if not token:
        raise HTTPException(status_code=400, detail="token required.")
    _get_auth().revoke_token(token)
    return {"ok": True}


@app.get("/auth/me")
def auth_me(token: str = ""):
    """
    Return the authenticated player's profile.
    Pass token as query param: GET /auth/me?token=<tok>
    """
    user = _require_auth(token)
    eng  = _get_engine()
    sim  = eng._sim_lookup.get(user.sim_id)
    sim_data: dict = {}
    if sim:
        sim_data = {
            "name":       sim.name,
            "age":        sim.profile.get("age", 0),
            "job":        sim.profile.get("job", ""),
            "simoleons":  round(sim.simoleons, 2),
            "emotion":    sim.emotion.dominant,
            "appearance": sim.profile.get("appearance", {}),
            "ocean":      sim.ocean,
            "traits":     sim.profile.get("traits", []),
            "mbti":       sim.profile.get("mbti", ""),
            "aspiration": sim.profile.get("aspiration", ""),
            "lod_tier":   sim.lod_tier.name,
        }
        if hasattr(eng, "web3"):
            wi = eng.web3.wallet_info(user.sim_id)
            sim_data["wallet"] = {
                "game_wallet":      wi["game_wallet"],
                "game_balance_sim": round(wi["game_balance_sim"], 4),
                "metamask_address": wi["metamask_address"],
                "metamask_linked":  wi["metamask_linked"],
                "chain_id":         wi["chain_id"],
            }
    return {**user.public_dict(), "sim": sim_data}


@app.put("/auth/sim/appearance")
def auth_update_appearance(body: dict):
    """
    Update the player's sim appearance.
    Body: {token, skin_tone?, hair_color?, hair_style?, eye_color?,
           build?, height?, style?, accessories?: [], bio?: str}
    """
    from core.appearance import validate_appearance, AppearanceValidationError
    token = str(body.get("token", "")).strip()
    user  = _require_auth(token)
    eng   = _get_engine()
    sim   = eng._sim_lookup.get(user.sim_id)
    if sim is None:
        raise HTTPException(status_code=404, detail="Sim not found in active engine.")

    # Merge incoming fields over existing appearance
    existing = sim.profile.get("appearance", {})
    merged   = {**existing, **{k: v for k, v in body.items()
                                if k not in ("token",) and v is not None}}
    try:
        appearance = validate_appearance(merged)
    except AppearanceValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    with _engine_lock:
        sim.profile["appearance"] = appearance.to_dict()

    return {
        "ok":         True,
        "sim_id":     user.sim_id,
        "appearance": appearance.to_dict(),
        "descriptor": appearance.to_profile_text(),
    }


@app.put("/auth/sim/personality")
def auth_update_personality(body: dict):
    """
    Update the player's sim personality.
    Body: {token, ocean?: {openness, …}, traits?: [], aspiration?: str, mbti?: str}

    OCEAN values must be in 0.0–1.0.  Unknown keys are silently ignored.
    Identity drift is NOT reset — prior drift history is preserved.
    """
    token = str(body.get("token", "")).strip()
    user  = _require_auth(token)
    eng   = _get_engine()
    sim   = eng._sim_lookup.get(user.sim_id)
    if sim is None:
        raise HTTPException(status_code=404, detail="Sim not found in active engine.")

    _OCEAN_KEYS = ("openness", "conscientiousness", "extraversion",
                   "agreeableness", "neuroticism")
    updated: dict = {}

    with _engine_lock:
        user_ocean = body.get("ocean", {}) or {}
        for key in _OCEAN_KEYS:
            if key in user_ocean:
                try:
                    val = float(user_ocean[key])
                    if 0.0 <= val <= 1.0:
                        sim.ocean[key] = round(val, 3)
                        updated[f"ocean.{key}"] = sim.ocean[key]
                except (TypeError, ValueError):
                    pass

        user_traits = body.get("traits", [])
        if isinstance(user_traits, list) and user_traits:
            sim.profile["traits"] = [str(t).strip()[:40] for t in user_traits[:5] if t]
            updated["traits"] = sim.profile["traits"]
            # Bust pair cache since traits affect attraction scoring
            if hasattr(eng, "_pair_cache"):
                eng._pair_cache.bump_sim(sim.sim_id)

        if body.get("aspiration"):
            sim.profile["aspiration"] = str(body["aspiration"]).strip()[:60]
            updated["aspiration"] = sim.profile["aspiration"]

        _VALID_MBTI = {
            "INTJ","INTP","ENTJ","ENTP","INFJ","INFP","ENFJ","ENFP",
            "ISTJ","ISFJ","ESTJ","ESFJ","ISTP","ISFP","ESTP","ESFP",
        }
        mbti = str(body.get("mbti", "")).strip().upper()
        if mbti in _VALID_MBTI:
            sim.profile["mbti"] = mbti
            updated["mbti"] = mbti

    return {
        "ok":     True,
        "sim_id": user.sim_id,
        "updated": updated,
        "current": {
            "ocean":      sim.ocean,
            "traits":     sim.profile.get("traits", []),
            "aspiration": sim.profile.get("aspiration", ""),
            "mbti":       sim.profile.get("mbti", ""),
        },
    }


@app.get("/auth/sim/{sim_id}/id-check")
def check_sim_id(sim_id: str):
    """
    Verify that a sim_id is unique across both the live engine and
    the auth database. Useful for client-side duplicate detection.
    """
    eng  = _get_engine()
    auth = _get_auth()
    in_engine = sim_id in eng._sim_lookup
    in_auth   = auth.sim_id_taken(sim_id)
    return {
        "sim_id":   sim_id,
        "unique":   not in_engine and not in_auth,
        "in_engine": in_engine,
        "in_auth":   in_auth,
    }


@app.get("/auth/stats")
def auth_stats():
    return _get_auth().stats()


# ─── City Bank API ────────────────────────────────────────────────────────────

@app.get("/bank/rates")
def bank_rates():
    """Current APR rates and term options for all deposit products."""
    from config import BANK_TERMS, BANK_MIN_DEPOSIT
    return {
        "minimum_deposit": BANK_MIN_DEPOSIT,
        "terms": {
            k: {
                "label":    v["label"],
                "duration": f"{v['seconds'] // 86400} days",
                "apr_pct":  round(v["apr"] * 100, 2),
                "example_interest_on_1000": round(
                    1000 * v["apr"] * (v["seconds"] / (365 * 86400)), 2
                ),
            }
            for k, v in BANK_TERMS.items()
        },
    }


@app.get("/bank/account/{sim_id}")
def bank_account(sim_id: str):
    """Full bank account view: checking balance + all deposits."""
    eng = _get_engine()
    if sim_id not in eng._sim_lookup:
        raise HTTPException(status_code=404, detail="sim not found")
    return eng.bank.full_account(sim_id)


@app.post("/bank/deposit")
def bank_deposit(body: dict):
    """
    Open a term deposit. Locks simoleons until maturity.

    Body: {sim_id, term_key, amount}
      term_key: "1_week" | "2_weeks" | "1_month" | "3_months" | "1_year"
      amount: simoleons to deposit (min §10)

    Returns the TermDeposit record including maturity timestamp.
    """
    eng      = _get_engine()
    sim_id   = str(body.get("sim_id", "")).strip()
    term_key = str(body.get("term_key", "")).strip()
    amount   = float(body.get("amount", 0))
    if not sim_id or not term_key or amount <= 0:
        raise HTTPException(status_code=400, detail="sim_id, term_key, amount required")
    sim = eng._sim_lookup.get(sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail="sim not found")
    try:
        with _engine_lock:
            dep = eng.bank.open_deposit(sim, term_key, amount, eng)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "ok": True,
        "deposit": dep.to_dict(),
        "sim_balance_after": round(sim.simoleons, 2),
    }


@app.post("/bank/withdraw")
def bank_withdraw(body: dict):
    """
    Withdraw a matured deposit. Fails if the term has not elapsed.

    Body: {sim_id, deposit_id}
    Returns: credited amount = principal + interest.
    """
    eng        = _get_engine()
    sim_id     = str(body.get("sim_id", "")).strip()
    deposit_id = str(body.get("deposit_id", "")).strip()
    if not sim_id or not deposit_id:
        raise HTTPException(status_code=400, detail="sim_id, deposit_id required")
    sim = eng._sim_lookup.get(sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail="sim not found")
    try:
        with _engine_lock:
            dep = eng.bank.withdraw(sim, deposit_id, eng)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "ok":             True,
        "deposit":        dep.to_dict(),
        "credited":       round(dep.matured_amount, 2),
        "interest_earned": round(dep.interest_earned, 2),
        "sim_balance_after": round(sim.simoleons, 2),
    }


@app.get("/bank/matured/{sim_id}")
def bank_matured(sim_id: str):
    """Deposits that have matured and are ready to withdraw."""
    eng = _get_engine()
    return {
        "sim_id":  sim_id,
        "matured": [d.to_dict() for d in eng.bank.matured_ready(sim_id)],
    }


@app.post("/bank/checking/deposit")
def bank_checking_deposit(body: dict):
    """Transfer simoleons from wallet to checking account (liquid, no interest)."""
    eng    = _get_engine()
    sim_id = str(body.get("sim_id", "")).strip()
    amount = float(body.get("amount", 0))
    sim    = eng._sim_lookup.get(sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail="sim not found")
    try:
        with _engine_lock:
            new_bal = eng.bank.deposit_to_checking(sim, amount, eng)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "checking_balance": new_bal, "sim_balance": round(sim.simoleons, 2)}


@app.post("/bank/checking/withdraw")
def bank_checking_withdraw(body: dict):
    """Transfer from checking account back to sim wallet."""
    eng    = _get_engine()
    sim_id = str(body.get("sim_id", "")).strip()
    amount = float(body.get("amount", 0))
    sim    = eng._sim_lookup.get(sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail="sim not found")
    try:
        with _engine_lock:
            new_bal = eng.bank.withdraw_from_checking(sim, amount, eng)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "checking_balance": new_bal, "sim_balance": round(sim.simoleons, 2)}


@app.get("/bank/stats")
def bank_stats():
    """Bank-wide statistics: total accounts, locked capital, deposit volumes."""
    eng = _get_engine()
    return eng.bank.stats()


# ─── Collateral API ────────────────────────────────────────────────────────────

@app.get("/collateral/{sim_id}")
def collateral_status(sim_id: str):
    """Active collateral record for a sim (if any)."""
    eng = _get_engine()
    rec = eng.collateral.active_for(sim_id)
    history = eng.collateral.history_for(sim_id)
    return {
        "sim_id":      sim_id,
        "active":      rec.to_dict() if rec else None,
        "history":     [r.to_dict() for r in history],
        "collateral_stats": eng.collateral.stats(),
    }


# ─── Heartbeat status ──────────────────────────────────────────────────────────

@app.get("/heartbeat/status")
def heartbeat_status():
    """Real-time heartbeat loop status and next scheduled event times."""
    import time as _t
    eng  = _get_engine()
    hb   = eng.heartbeat
    now  = _t.time()
    return {
        "running":          hb._running,
        "interval_seconds": __import__("config").HEARTBEAT_INTERVAL,
        "next_events": {
            k: round(max(0, v - now), 1)
            for k, v in hb._next.items()
        },
        "need_decay_rates_per_hour": {
            k: round(v * 3600, 4)
            for k, v in __import__("config").NEED_DECAY_RATES.items()
        },
    }


# ─── ACID Financial Ledger API ────────────────────────────────────────────────

@app.get("/ledger/summary")
def ledger_summary(since_tick: int = 0):
    """Global ledger health: total txs, flagged entries, total volume."""
    eng = _get_engine()
    return eng.financial_ledger.summary(since_tick=since_tick)


@app.get("/ledger/sim/{sim_id}")
def ledger_sim_history(sim_id: str, limit: int = 100, since_tick: int = 0,
                       tx_type: str = ""):
    """Full transaction history for one sim, newest first."""
    eng = _get_engine()
    entries = eng.financial_ledger.history(
        sim_id, limit=limit, since_tick=since_tick,
        tx_type=tx_type if tx_type else None,
    )
    return {"sim_id": sim_id, "count": len(entries),
            "entries": [e.to_dict() for e in entries]}


@app.get("/ledger/sim/{sim_id}/income")
def ledger_sim_income(sim_id: str, since_tick: int = 0):
    """Income breakdown by source type — exposes exactly how a sim earned."""
    eng = _get_engine()
    return {
        "sim_id":   sim_id,
        "income":   eng.financial_ledger.income_breakdown(sim_id, since_tick),
        "expenses": eng.financial_ledger.expense_breakdown(sim_id, since_tick),
        "velocity": eng.financial_ledger.wealth_velocity(sim_id),
    }


@app.get("/ledger/sim/{sim_id}/balance_at/{tick}")
def ledger_balance_at(sim_id: str, tick: int):
    """Reconstruct a sim's simoleon balance at any historical tick."""
    eng = _get_engine()
    bal = eng.financial_ledger.balance_at_tick(sim_id, tick)
    return {"sim_id": sim_id, "tick": tick, "balance": bal}


@app.get("/ledger/sim/{sim_id}/wealth_history")
def ledger_wealth_history(sim_id: str):
    """Wealth curve over time (snapshot checkpoints)."""
    eng = _get_engine()
    return {"sim_id": sim_id,
            "history": eng.financial_ledger.net_worth_history(sim_id)}


@app.get("/ledger/top_earners")
def ledger_top_earners(since_tick: int = 0, limit: int = 10):
    """Ranked list of highest total earners since a given tick."""
    eng = _get_engine()
    earners = eng.financial_ledger.top_earners(since_tick=since_tick, limit=limit)
    # Enrich with sim names
    for row in earners:
        sim = eng._sim_lookup.get(row["sim_id"])
        row["name"] = sim.name if sim else "unknown"
        row["job"]  = sim.profile.get("job", "") if sim else ""
    return {"since_tick": since_tick, "earners": earners}


@app.get("/ledger/anomalies")
def ledger_anomalies(since_tick: int = 0, limit: int = 50):
    """All flagged transactions — entries exceeding per-type or global ceilings."""
    eng     = _get_engine()
    entries = eng.financial_ledger.anomalies(since_tick=since_tick, limit=limit)
    result  = []
    for e in entries:
        sim = eng._sim_lookup.get(e.sim_id)
        d   = e.to_dict()
        d["sim_name"] = sim.name if sim else "unknown"
        result.append(d)
    return {"count": len(result), "anomalies": result}


@app.get("/ledger/trace/{sim_id}")
def ledger_trace(sim_id: str):
    """
    Full wealth trace for one sim: income by source, top transactions,
    anomalies, balance curve. The tool that would have found Michael's 886M.
    """
    eng = _get_engine()
    sim = eng._sim_lookup.get(sim_id)
    if sim is None:
        raise HTTPException(status_code=404, detail="sim not found")

    fl    = eng.financial_ledger
    top10 = fl.history(sim_id, limit=10)
    anom  = fl.anomalies(limit=20)
    anom_for_sim = [e for e in anom if e.sim_id == sim_id]

    return {
        "sim_id":          sim_id,
        "sim_name":        sim.name,
        "current_balance": round(sim.simoleons, 2),
        "income_by_source": fl.income_breakdown(sim_id),
        "expense_by_type":  fl.expense_breakdown(sim_id),
        "wealth_velocity":  fl.wealth_velocity(sim_id),
        "wealth_history":   fl.net_worth_history(sim_id),
        "recent_transactions": [e.to_dict() for e in top10],
        "flagged_transactions": [e.to_dict() for e in anom_for_sim],
    }


# ─── WebSocket stream ─────────────────────────────────────────────────────────


@app.websocket("/stream")
async def stream(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    try:
        while True:
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        _ws_clients.remove(ws)


@app.websocket("/online/ws")
async def online_ws(ws: WebSocket):
    token = ws.query_params.get("token", "")
    sess = _online_world.get_session(str(token))
    if sess is None:
        await ws.close(code=4401)
        return
    _online_world.touch(sess, connected=True)
    await ws.accept()
    _online_ws_clients.append(ws)
    try:
        while True:
            msg = await ws.receive_text()
            try:
                payload = json.loads(msg)
            except Exception:
                payload = {"type": "ping"}
            if payload.get("type") == "ping":
                await ws.send_json({"type": "pong", "ts": round(time.time(), 3)})
    except WebSocketDisconnect:
        _online_world.touch(sess, connected=False)
        if ws in _online_ws_clients:
            _online_ws_clients.remove(ws)


async def _broadcast(state: dict):
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json(state)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)


async def _broadcast_online_rooms() -> None:
    eng = _get_engine()
    dead = []
    for ws in _online_ws_clients:
        try:
            token = ws.query_params.get("token", "")
            sess = _online_world.get_session(str(token))
            if sess is None:
                continue
            payload = _online_world.room_payload(eng, sess.room_id)
            if sess.sim_id:
                mine = next(
                    (s for s in payload.get("sims", []) if s.get("id") == sess.sim_id),
                    None,
                )
                if mine is not None:
                    payload["you"] = {
                        "sim_id": sess.sim_id,
                        "hints": _action_hints_for_sim(mine),
                    }
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _online_ws_clients:
            _online_ws_clients.remove(ws)


async def _online_world_loop() -> None:
    while True:
        await asyncio.sleep(1.0)
        eng = _get_engine()
        with _engine_lock:
            eng.heartbeat.beat_once()
            _online_world.cleanup_expired_sessions()
        await _broadcast_online_rooms()


@app.get("/ui", response_class=HTMLResponse)
def online_ui():
    html = """
<!doctype html><html><head><meta charset='utf-8'/><meta name='viewport' content='width=device-width,initial-scale=1'/><title>Sims Online</title>
<style>body{font-family:ui-sans-serif;background:#f8f2e9;color:#222;margin:0}#app{max-width:980px;margin:0 auto;padding:16px}.row{display:flex;gap:12px;flex-wrap:wrap}.card{background:#fff;border:1px solid #ddd;border-radius:12px;padding:12px}.col{flex:1;min-width:280px}button{padding:8px 10px;border-radius:8px;border:1px solid #bbb;background:#f4f4f4;cursor:pointer}input,select{padding:8px;border:1px solid #bbb;border-radius:8px;width:100%}pre{white-space:pre-wrap;max-height:240px;overflow:auto;background:#111;color:#d8ffd8;padding:10px;border-radius:8px}</style>
</head><body><div id='app'>
<h2>Tamagotchi Online</h2>
<div class='row'>
<div class='card col'><h3>Session</h3><input id='username' placeholder='username'/><button onclick='createSession()'>Create Session</button><div id='sess'></div><input id='simid' placeholder='sim_id to claim'/><button onclick='claimSim()'>Claim Sim</button><input id='roomid' placeholder='room id' value='global'/><button onclick='joinRoom()'>Join Room</button></div>
<div class='card col'><h3>Actions</h3><div class='row'><button onclick="act('chat')">Chat</button><button onclick="act('interact')">Interact</button><button onclick="act('buy')">Buy</button><button onclick="act('sell')">Sell</button><button onclick="act('use')">Use</button></div><input id='arg' placeholder='JSON args e.g. {"text":"hello"}'/></div>
</div>
<div class='card'><h3>Your Hints</h3><pre id='hints'></pre></div>
<div class='card'><h3>Room Feed</h3><pre id='feed'></pre></div>
</div>
<script>
let token=''; let ws=null;
function log(x){const el=document.getElementById('feed'); el.textContent = (typeof x==='string'?x:JSON.stringify(x,null,2)) + '\n' + el.textContent;}
function setHints(x){document.getElementById('hints').textContent = typeof x==='string'?x:JSON.stringify(x,null,2);}
async function createSession(){const username=document.getElementById('username').value; const r=await fetch('/online/session',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({username})}); const d=await r.json(); token=d.token||''; document.getElementById('sess').textContent=JSON.stringify(d); openWs();}
async function claimSim(){const sim_id=document.getElementById('simid').value; const r=await fetch('/online/claim',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({token,sim_id})}); log(await r.json());}
async function joinRoom(){const room_id=document.getElementById('roomid').value; const r=await fetch('/online/room/join',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({token,room_id})}); log(await r.json());}
async function act(cmd){let args={}; try{args=JSON.parse(document.getElementById('arg').value||'{}')}catch(e){alert('invalid args json');return;} const r=await fetch('/online/action',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({token,command:cmd,args})}); log(await r.json());}
function openWs(){ if(ws){ws.close();} ws = new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/online/ws?token='+encodeURIComponent(token)); ws.onmessage=(e)=>{try{const d=JSON.parse(e.data); if(d.you&&d.you.hints){setHints(d.you.hints);} log(d)}catch(_){log(e.data)}}; ws.onopen=()=>log('ws connected'); ws.onclose=()=>log('ws closed'); }
</script></body></html>
"""
    return HTMLResponse(content=html)


# ─── Startup ──────────────────────────────────────────────────────────────────


@app.on_event("startup")
async def startup():
    global _engine, _online_broadcast_loop, _online_world_task
    _engine = _build_engine(_args.sims)
    # Capture the event loop here (startup always runs in the async context).
    # tick() runs in a thread pool, so asyncio.create_task() would fail there —
    # use run_coroutine_threadsafe to safely schedule broadcasts from any thread.
    _loop = asyncio.get_event_loop()
    _online_broadcast_loop = _loop
    _engine._bus.on(
        "tick_complete",
        lambda **kw: asyncio.run_coroutine_threadsafe(
            _broadcast(_engine.get_state()), _loop
        ),
    )
    _engine._bus.on(
        "tick_complete",
        lambda **kw: asyncio.run_coroutine_threadsafe(_broadcast_online_rooms(), _loop),
    )
    _online_world_task = asyncio.create_task(_online_world_loop())

    # Real-time heartbeat loop (replaces tick-based scheduling)
    asyncio.create_task(_engine.heartbeat.run())
    print(f"\n[API] Sims Engine ready — {len(_engine.sims)} sims")
    print(f"[API] Real-time heartbeat running every {__import__('config').HEARTBEAT_INTERVAL:.0f}s")
    print(
        "[API] GET /state  POST /tick  GET /sim/{id}  GET /profile/{id}  POST /interact  WS /stream  GET /timings\n"
    )


if __name__ == "__main__":
    import os as _os

    parser = argparse.ArgumentParser(description="Sims Engine API server")
    parser.add_argument("--sims", type=int, default=3)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument(
        "--backend",
        default="llama-server",
        choices=["llama-server", "llama-cpp", "mock"],
    )
    parser.add_argument("--no-datasets", action="store_true")
    parser.add_argument("--no-restore", action="store_true")
    _args = parser.parse_args()
    uvicorn.run(app, host=_args.host, port=_args.port, log_level="warning")
