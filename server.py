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
    POST /interact          force a specific interaction
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
_args: argparse.Namespace = argparse.Namespace(sims=3, port=8080, host="0.0.0.0", backend="ollama", no_datasets=False)


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
        sims = [Sim(generate_sim_profile(okcupid_essays=essays or None)) for _ in range(num_sims)]

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
    print("[API] GET /state  POST /tick  GET /sim/{id}  POST /interact  WS /stream  GET /timings\n")


if __name__ == "__main__":
    import os as _os
    parser = argparse.ArgumentParser(description="Sims Engine API server")
    parser.add_argument("--sims", type=int, default=3)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--backend", default="ollama", choices=["ollama", "llama-server", "llama-cpp"])
    parser.add_argument("--ollama-model", default=None, help="Ollama model name (e.g. qwen2.5:3b)")
    parser.add_argument("--ollama-url", default=None, help="Ollama API URL")
    parser.add_argument("--no-datasets", action="store_true")
    _args = parser.parse_args()
    if _args.ollama_model:
        _os.environ["SIM_V2_OLLAMA_MODEL"] = _args.ollama_model
    if _args.ollama_url:
        _os.environ["SIM_V2_OLLAMA_URL"] = _args.ollama_url
    uvicorn.run(app, host=_args.host, port=_args.port, log_level="warning")
