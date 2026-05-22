"""
engine/heartbeat.py — Real-time heartbeat loop replacing tick-based scheduling.

Instead of advancing a tick counter, the heartbeat computes elapsed real seconds
(dt) since the last run and applies proportional rates:

  sim.needs.hunger -= HUNGER_RATE_PER_SEC * dt

Cadenced operations (career events, relationship decay, autosave) fire when
their real-time threshold has elapsed — no tick math involved.

The server calls heartbeat.start() on startup which launches an asyncio task.
Each beat runs SimEngine.heartbeat(dt) then sleeps HEARTBEAT_INTERVAL seconds.

Bank maturity checking and collateral evaluation happen every beat automatically.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.engine import SimEngine

from config import (
    HEARTBEAT_INTERVAL,
    NEED_DECAY_RATES,
    RT_CAREER_EVENT_INTERVAL,
    RT_LIFE_EVENT_INTERVAL,
    RT_GOSSIP_INTERVAL,
    RT_RELATIONSHIP_DECAY,
    RT_VENUE_ROTATION,
    RT_AUTOSAVE_INTERVAL,
    RT_SNAPSHOT_INTERVAL,
    COLLATERAL_TRIGGER_BALANCE,
    NEEDS_DECAY,         # kept for legacy callers
)

logger = logging.getLogger(__name__)


class HeartbeatLoop:
    """
    Owns the real-time scheduling for a SimEngine.

    Usage:
        loop = HeartbeatLoop(engine)
        # In asyncio context:
        asyncio.create_task(loop.run())
        # Or in a thread:
        loop.run_sync()
    """

    def __init__(self, engine: "SimEngine") -> None:
        self.engine   = engine
        self._running = False
        self._last    = time.time()

        # Real-time thresholds (Unix timestamps of next fire)
        now = self._last
        self._next: dict[str, float] = {
            "career_event":    now + RT_CAREER_EVENT_INTERVAL,
            "life_event":      now + RT_LIFE_EVENT_INTERVAL,
            "gossip":          now + RT_GOSSIP_INTERVAL,
            "rel_decay":       now + RT_RELATIONSHIP_DECAY,
            "venue_rotation":  now + RT_VENUE_ROTATION,
            "autosave":        now + RT_AUTOSAVE_INTERVAL,
            "snapshot":        now + RT_SNAPSHOT_INTERVAL,
        }

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """asyncio task — runs indefinitely until stopped."""
        self._running = True
        logger.info("[Heartbeat] Real-time loop started (interval=%.1fs)", HEARTBEAT_INTERVAL)
        while self._running:
            try:
                now = time.time()
                dt  = now - self._last
                self._last = now
                self._beat(dt, now)
            except Exception as exc:
                logger.error("[Heartbeat] beat error: %s", exc, exc_info=True)
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    def run_sync(self) -> None:
        """Blocking run for thread-based servers."""
        import time as _time
        self._running = True
        logger.info("[Heartbeat] Sync real-time loop started")
        while self._running:
            try:
                now = _time.time()
                dt  = now - self._last
                self._last = now
                self._beat(dt, now)
            except Exception as exc:
                logger.error("[Heartbeat] beat error: %s", exc, exc_info=True)
            _time.sleep(HEARTBEAT_INTERVAL)

    def stop(self) -> None:
        self._running = False

    def beat_once(self) -> None:
        """Single manual beat — useful for tests and CLI mode."""
        now = time.time()
        dt  = now - self._last
        self._last = now
        self._beat(dt, now)

    # ── Core beat ─────────────────────────────────────────────────────────────

    def _beat(self, dt: float, now: float) -> None:
        eng = self.engine

        # ── 1. Need decay — proportional to real elapsed seconds ──────────────
        self._decay_needs(dt)

        # ── 2. Drain LLM async futures ────────────────────────────────────────
        try:
            eng.process_pending()
        except Exception as exc:
            logger.debug("[Heartbeat] process_pending: %s", exc)

        # ── 3. Per-sim arc / goal / dream processing (budgeted) ───────────────
        try:
            eng.process_sims(dt)
        except Exception as exc:
            logger.debug("[Heartbeat] process_sims: %s", exc)

        # ── 4. Autonomous self-care ───────────────────────────────────────────
        self._self_care()

        # ── 5. World / economy systems ────────────────────────────────────────
        try:
            eng.tick_world_systems(now)
        except Exception as exc:
            logger.debug("[Heartbeat] tick_world_systems: %s", exc)

        # ── 6. Emergent social / narrative systems ────────────────────────────
        try:
            eng.tick_emergent_systems(now)
        except Exception as exc:
            logger.debug("[Heartbeat] tick_emergent_systems: %s", exc)

        # ── 7. Bank maturity check ────────────────────────────────────────────
        if hasattr(eng, "bank"):
            try:
                eng.bank.check_maturities(eng)
            except Exception as exc:
                logger.debug("[Heartbeat] bank check: %s", exc)

        # ── 8. Collateral evaluation ──────────────────────────────────────────
        if hasattr(eng, "collateral"):
            for sim in eng.sims:
                if sim.simoleons < COLLATERAL_TRIGGER_BALANCE:
                    try:
                        eng.collateral.evaluate(sim, eng)
                    except Exception:
                        pass

        # ── 9. Cadenced real-time events (gossip, career, decay, autosave …) ──
        self._fire_cadenced(now)

        # ── 10. LLM pair selection (budget-limited, one per beat) ─────────────
        try:
            self._maybe_interact()
        except Exception as exc:
            logger.debug("[Heartbeat] interaction: %s", exc)

        # ── 11. Heartbeat event ───────────────────────────────────────────────
        try:
            eng._bus.emit("heartbeat", dt=dt, now=now)
        except Exception:
            pass

    # ── Need decay ────────────────────────────────────────────────────────────

    def _decay_needs(self, dt: float) -> None:
        """Apply per-second decay rates to all non-dormant sims."""
        from sim_types.enums import LODTier
        for sim in self.engine.sims:
            if getattr(sim, "lod_tier", LODTier.ACTIVE) == LODTier.DORMANT:
                # Dormant: only hunger/energy at half rate
                sim.needs.hunger = max(0.0,
                    sim.needs.hunger - NEED_DECAY_RATES["hunger"] * dt * 0.5)
                sim.needs.energy = max(0.0,
                    sim.needs.energy - NEED_DECAY_RATES["energy"] * dt * 0.5)
                continue

            for need_name, rate in NEED_DECAY_RATES.items():
                current = getattr(sim.needs, need_name, None)
                if current is not None:
                    setattr(sim.needs, need_name, max(0.0, current - rate * dt))

    # ── Autonomous self-care ──────────────────────────────────────────────────

    def _self_care(self) -> None:
        """Sims autonomously address critical needs between player sessions."""
        from config import (
            SLEEP_ENERGY_THRESHOLD, SLEEP_ENERGY_RESTORE, SLEEP_WAKE_THRESHOLD,
            HUNGER_HOME_THRESHOLD, HUNGER_HOME_RESTORE,
            BLADDER_FLUSH_THRESHOLD, BLADDER_RESTORE,
            HYGIENE_SHOWER_THRESHOLD, HYGIENE_RESTORE,
        )
        # Scale restore amounts to one heartbeat
        scale = HEARTBEAT_INTERVAL / 3600.0  # fraction of an hour
        for sim in self.engine.sims:
            n = sim.needs
            # Sleep
            if n.energy < SLEEP_ENERGY_THRESHOLD:
                if not getattr(sim, "_sleeping", False):
                    sim._sleeping = True
                n.energy = min(100.0, n.energy + SLEEP_ENERGY_RESTORE * scale)
            elif getattr(sim, "_sleeping", False) and n.energy >= SLEEP_WAKE_THRESHOLD:
                sim._sleeping = False
            # Eat
            if n.hunger < HUNGER_HOME_THRESHOLD:
                n.hunger = min(100.0, n.hunger + HUNGER_HOME_RESTORE * scale)
            # Bathroom
            if n.bladder < BLADDER_FLUSH_THRESHOLD:
                n.bladder = min(100.0, n.bladder + BLADDER_RESTORE * scale)
            # Shower
            if n.hygiene < HYGIENE_SHOWER_THRESHOLD:
                n.hygiene = min(100.0, n.hygiene + HYGIENE_RESTORE * scale)

    # ── Cadenced real-time events ─────────────────────────────────────────────

    def _fire_cadenced(self, now: float) -> None:
        eng = self.engine

        if now >= self._next["rel_decay"]:
            self._next["rel_decay"] = now + RT_RELATIONSHIP_DECAY
            try:
                eng.relationships.decay_all()
            except Exception:
                pass

        if now >= self._next["gossip"]:
            self._next["gossip"] = now + RT_GOSSIP_INTERVAL
            try:
                if len(eng.sims) >= 3:
                    from config import GOSSIP_SPREAD_CHANCE
                    spreader = random.choice(eng.sims)
                    rest     = [s for s in eng.sims if s is not spreader]
                    receiver = random.choice(rest)
                    subjects = [s for s in rest if s is not receiver]
                    if subjects and random.random() < GOSSIP_SPREAD_CHANCE:
                        eng.gossip.spread(
                            spreader.sim_id, receiver.sim_id,
                            random.choice(subjects).sim_id,
                        )
            except Exception:
                pass

        if now >= self._next["career_event"]:
            self._next["career_event"] = now + RT_CAREER_EVENT_INTERVAL
            try:
                from config import CAREER_EVENT_CHANCE
                if random.random() < CAREER_EVENT_CHANCE and eng.sims:
                    eng._run_career_event(random.choice(eng.sims))
            except Exception:
                pass

        if now >= self._next["venue_rotation"]:
            self._next["venue_rotation"] = now + RT_VENUE_ROTATION
            try:
                import random as _r
                from world.venues import VENUES
                eng._venue = {**_r.choice(VENUES), **eng._audio_sensor.sense()}
            except Exception:
                pass

        if now >= self._next["autosave"]:
            self._next["autosave"] = now + RT_AUTOSAVE_INTERVAL
            try:
                if eng._db:
                    eng._db.save_state(eng)
                logger.debug("[Heartbeat] autosaved")
            except Exception as exc:
                logger.warning("[Heartbeat] autosave failed: %s", exc)

        if now >= self._next["snapshot"]:
            self._next["snapshot"] = now + RT_SNAPSHOT_INTERVAL
            try:
                eng._event_log.write_snapshot(int(now), eng.get_state())
                for sim in eng.sims:
                    eng.financial_ledger.snapshot_balance(sim.sim_id, int(now), sim.simoleons)
            except Exception:
                pass

    # ── LLM interactions ──────────────────────────────────────────────────────

    def _maybe_interact(self) -> None:
        """
        Trigger NPC-vs-NPC interactions at real-time pacing.
        Active sims interact roughly once every few minutes in background.
        Player-owned sims interact on player input.
        """
        eng = self.engine
        from sim_types.enums import LODTier
        active = [
            s for s in eng.sims
            if s.lod_tier == LODTier.ACTIVE and not getattr(s, "_sleeping", False)
        ]
        if len(active) < 2 or eng._pending:
            return

        # One background interaction per beat (rate-limited)
        from engine.scheduler import pick_interaction_pair, choose_interaction
        pair = pick_interaction_pair(active, eng.relationships)
        if pair:
            sim_a, sim_b = pair
            interaction  = choose_interaction(
                sim_a, sim_b,
                eng.relationships.get(sim_a.sim_id, sim_b.sim_id),
                int(time.time()),
                eng._datasets,
            )
            eng._submit_interaction(sim_a, sim_b, interaction, eng._venue)
