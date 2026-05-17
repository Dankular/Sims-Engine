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
    GET  /items/lot/{lot_id}  list lot stock
    GET  /items/search        search global item catalog
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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
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
_args: argparse.Namespace = argparse.Namespace(
    sims=3, port=8080, host="0.0.0.0", backend="ollama", no_datasets=False
)


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

    with timing_store.phase("engine_init"):
        engine = SimEngine(sims=sims, llm=llm, datasets=datasets, db=db)
        engine.households = households

    boot_total = timing_store.finish_boot()
    timing_store.print_boot()
    return engine


# ─── REST endpoints ──────────────────────────────────────────────────────────


@app.get("/state")
def get_state():
    return _get_engine().get_state()


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
        eng.run_tick()
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
            sentiments.append({
                "name": s.name,
                "added_tick": s.added_tick,
                "expires_tick": s.expires_tick,
                "source": s.source,
                "valence": cat.valence if cat else None,
                "permanent": s.expires_tick == -1,
            })
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
        for n in ["hunger", "energy", "social", "fun", "hygiene", "environment", "bladder", "comfort"]
    }
    needs_detail = {
        n: {"value": v, "critical": v < 20, "low": v < 40}
        for n, v in needs_raw.items()
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
        "zodiac": sim.profile.get("zodiac", {}).get("sign") if isinstance(sim.profile.get("zodiac"), dict) else sim.profile.get("zodiac"),
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
            "ts4_emotion": sim.moodlets.ts4_emotion() if hasattr(sim, "moodlets") else "Fine",
            "ts4_intensity": sim.moodlets.ts4_intensity() if hasattr(sim, "moodlets") else 0,
            "ts4_color": sim.moodlets.ts4_color() if hasattr(sim, "moodlets") else "#e9e9e9",
        },
        "moodlets": moodlet_detail,
        "fears": [{"label": f.label, "severity": round(f.severity, 2)} for f in sim.fears],
        "active_wants": [{"description": w.description, "priority": w.priority, "target": w.target_sim} for w in sim.active_wants],
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
        "inventory": list(sim.inventory),
        "inventory_objects": list(getattr(sim, "inventory_objects", [])),
        "properties": list(sim.properties),
        "owned_businesses": list(sim.owned_businesses),
        "property_value": round(eng.properties.total_portfolio_value(sim_id), 2),
        "perks": sorted(sim.perks),
        "perk_points": sim.perk_points,
        "last_dream": getattr(sim, "_last_dream", None),
        "travel_history": list(sim.travel_history),
        "pet_ids": list(sim.pet_ids),
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


@app.get("/items/lot/{lot_id}")
def get_lot_items(lot_id: str):
    eng = _get_engine()
    return {
        "lot_id": lot_id,
        "items": eng.objects.lot_stock_state(lot_id),
    }


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


async def _broadcast(state: dict):
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json(state)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)


# ─── Startup ──────────────────────────────────────────────────────────────────


@app.on_event("startup")
async def startup():
    global _engine
    _engine = _build_engine(_args.sims)
    # Capture the event loop here (startup always runs in the async context).
    # tick() runs in a thread pool, so asyncio.create_task() would fail there —
    # use run_coroutine_threadsafe to safely schedule broadcasts from any thread.
    _loop = asyncio.get_event_loop()
    _engine._bus.on(
        "tick_complete",
        lambda **kw: asyncio.run_coroutine_threadsafe(
            _broadcast(_engine.get_state()), _loop
        ),
    )
    print(f"\n[API] Sims Engine ready — {len(_engine.sims)} sims")
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
        "--backend", default="ollama", choices=["ollama", "llama-server", "llama-cpp"]
    )
    parser.add_argument(
        "--ollama-model", default=None, help="Ollama model name (e.g. qwen2.5:3b)"
    )
    parser.add_argument("--ollama-url", default=None, help="Ollama API URL")
    parser.add_argument("--no-datasets", action="store_true")
    _args = parser.parse_args()
    if _args.ollama_model:
        _os.environ["SIM_V2_OLLAMA_MODEL"] = _args.ollama_model
    if _args.ollama_url:
        _os.environ["SIM_V2_OLLAMA_URL"] = _args.ollama_url
    uvicorn.run(app, host=_args.host, port=_args.port, log_level="warning")
