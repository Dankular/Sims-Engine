"""
engine/realtime.py — Real-time game loop adapter for SimEngine.

RealtimeSimEngine wraps the existing SimEngine and replaces the blocking
run_tick() loop with a non-blocking update() that is safe to call at
60 fps from any game loop or thread.

Architecture
────────────
  Game loop thread  →  rt.update()  →  schedules / drains LLM futures
                    →  rt.get_state() → read current state (always fast)

  Background thread pool  →  LLM adjudication (unchanged)

Time model
──────────
  Wall time  (time.monotonic()) — the real clock
  Sim  time  (SimClock.sim_now) — compressed in-game datetime
  Age        — sim birth_date compared to sim_now (continuous, not stepped)

Needs decay, arc ticks, and interaction scheduling all scale with real
elapsed time so the engine behaves consistently at any sim speed.

Speed guide (sim_speed = sim_seconds per real_second):
  3_600  → 1 sim hr/real sec  — full life ≈ 2.4 real hours  (default)
  86_400 → 1 sim day/real sec — full life ≈ 2 real hours
  525_600 → 1 sim yr/real min — full life ≈ 75 real minutes
"""
from __future__ import annotations

import logging
import time
import threading
from typing import TYPE_CHECKING

from engine.clock import SimClock, DEFAULT_SIM_SPEED
from engine.async_adj import drain_pending
from config import NEEDS_DECAY, TICKS_PER_YEAR

if TYPE_CHECKING:
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)

# ── Need decay rates ──────────────────────────────────────────────────────────
# Derived from per-tick rates so behaviour is consistent with tick mode.
# NEEDS_DECAY is applied per tick; 1 tick ≈ (365*24/TICKS_PER_YEAR) sim hours.
_SIM_HOURS_PER_TICK: float = (365.25 * 24) / TICKS_PER_YEAR      # ≈ 175 h
_HUNGER_RATE: float  = NEEDS_DECAY           / _SIM_HOURS_PER_TICK  # /sim-hr
_ENERGY_RATE: float  = NEEDS_DECAY * 0.80    / _SIM_HOURS_PER_TICK
_SOCIAL_RATE: float  = NEEDS_DECAY * 0.90    / _SIM_HOURS_PER_TICK  # base; extrav scales
_FUN_RATE:    float  = NEEDS_DECAY * 0.90    / _SIM_HOURS_PER_TICK
_HYGIENE_RATE:float  = NEEDS_DECAY * 0.50    / _SIM_HOURS_PER_TICK
_BLADDER_RATE:float  = NEEDS_DECAY * 1.20    / _SIM_HOURS_PER_TICK
_COMFORT_RATE:float  = NEEDS_DECAY * 0.40    / _SIM_HOURS_PER_TICK
_ENV_RATE:    float  = NEEDS_DECAY * 0.30    / _SIM_HOURS_PER_TICK

# Arc tick cadence (fire arcs every N sim hours)
_ARC_SIM_HOUR_INTERVAL: float = 1.0

# Minimum real seconds between interaction opportunities per pair
_MIN_INTERACTION_INTERVAL: float = 8.0   # real seconds at default speed


class RealtimeSimEngine:
    """
    Non-blocking real-time wrapper around SimEngine.

    Call update() from your game loop — it returns immediately.
    Call get_state() whenever you need to render.
    """

    def __init__(
        self,
        engine: "SimEngine",
        speed: float = DEFAULT_SIM_SPEED,
        start_date=None,
    ):
        self._engine = engine
        self.clock   = SimClock(speed=speed, start_date=start_date)
        self._lock   = threading.Lock()

        self._last_wall: float  = time.monotonic()
        self._last_arc_wall: float = time.monotonic()

        # Per-pair: wall time of last submitted interaction
        self._pair_last_interaction: dict[tuple, float] = {}

        # Per-sim: last checked sim-year for birthday detection
        self._sim_birth_years: dict[str, int] = {}

        # Register birth years from starting sims
        for sim in engine.sims:
            self._register_birth(sim)

    # ── Main game-loop entrypoint ─────────────────────────────────────────────

    def update(self) -> None:
        """Non-blocking update. Safe to call at 60 fps."""
        now     = time.monotonic()
        elapsed = now - self._last_wall
        self._last_wall = now

        with self._lock:
            self._drain_futures()
            self._update_needs(elapsed)
            self._check_interactions(now)
            self._tick_arcs(now)
            self._check_aging()

    # ── State access ──────────────────────────────────────────────────────────

    def get_state(self) -> dict:
        state = self._engine.get_state()
        state["sim_time"]    = self.clock.sim_now.isoformat()
        state["sim_label"]   = self.clock.label()
        state["wall_epoch"]  = time.time()
        state["sim_speed"]   = self.clock.speed
        state["speed_label"] = self.clock.speed_label()
        return state

    def set_speed(self, speed: float) -> None:
        self.clock.set_speed(speed)

    @property
    def sims(self):
        return self._engine.sims

    @property
    def all_sims_dead(self) -> bool:
        return self._engine.all_sims_dead

    def shutdown(self) -> None:
        self._engine.shutdown()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _drain_futures(self) -> None:
        done, self._engine._pending = drain_pending(self._engine._pending)
        for item in done:
            try:
                self._engine._apply_resolved(item, item.future.result())
            except Exception as exc:
                logger.warning("Adjudicator failed: %s", exc)

    def _update_needs(self, elapsed_real: float) -> None:
        """Rate-based need decay + autonomous self-care."""
        sim_hours = elapsed_real * self.clock.speed / 3_600.0
        from config import (
            SLEEP_ENERGY_THRESHOLD, SLEEP_ENERGY_RESTORE, SLEEP_WAKE_THRESHOLD,
            HUNGER_HOME_THRESHOLD, HUNGER_HOME_RESTORE,
            BLADDER_FLUSH_THRESHOLD, BLADDER_RESTORE,
            HYGIENE_SHOWER_THRESHOLD, HYGIENE_RESTORE,
        )
        restore_rate = sim_hours / _SIM_HOURS_PER_TICK  # fractional tick equivalent

        for sim in self._engine.sims:
            n = sim.needs
            extrav_mod = 1.2 if sim.ocean.get("extraversion", 0.5) > 0.6 else 0.7

            # Decay
            n.hunger    = max(0.0, n.hunger    - sim_hours * _HUNGER_RATE)
            n.energy    = max(0.0, n.energy    - sim_hours * _ENERGY_RATE)
            n.social    = max(0.0, n.social    - sim_hours * _SOCIAL_RATE * extrav_mod)
            n.fun       = max(0.0, n.fun       - sim_hours * _FUN_RATE)
            n.hygiene   = max(0.0, n.hygiene   - sim_hours * _HYGIENE_RATE)
            n.bladder   = max(0.0, n.bladder   - sim_hours * _BLADDER_RATE)
            n.comfort   = max(0.0, n.comfort   - sim_hours * _COMFORT_RATE)
            n.environment = max(0.0, n.environment - sim_hours * _ENV_RATE)

            # Autonomous self-care (proportional to elapsed time)
            if n.energy < SLEEP_ENERGY_THRESHOLD:
                if not getattr(sim, "_sleeping", False):
                    sim._sleeping = True
                    sim.emotion.add("relief", 0.3, duration=3, source="sleep")
                n.energy = min(100.0, n.energy + SLEEP_ENERGY_RESTORE * restore_rate)
            elif getattr(sim, "_sleeping", False):
                if n.energy >= SLEEP_WAKE_THRESHOLD:
                    sim._sleeping = False
                    sim.emotion.add("optimism", 0.4, duration=4, source="well_rested")
                else:
                    n.energy = min(100.0, n.energy + SLEEP_ENERGY_RESTORE * restore_rate)

            if n.hunger  < HUNGER_HOME_THRESHOLD:
                n.hunger  = min(100.0, n.hunger  + HUNGER_HOME_RESTORE  * restore_rate)
            if n.bladder < BLADDER_FLUSH_THRESHOLD:
                n.bladder = min(100.0, n.bladder + BLADDER_RESTORE       * restore_rate)
            if n.hygiene < HYGIENE_SHOWER_THRESHOLD:
                n.hygiene = min(100.0, n.hygiene + HYGIENE_RESTORE       * restore_rate)

            # Critical need emotions
            for need in n.critical_needs():
                label = "annoyance" if need in ("bladder", "hunger") else "discomfort"
                sim.emotion.add(label, 0.7, duration=2, source=f"critical:{need}")

    def _check_interactions(self, now: float) -> None:
        """Queue interactions for eligible pairs whose interval has elapsed."""
        if self._engine._pending:
            return   # wait for current LLM call to finish

        from sim_types.enums import LODTier
        from engine.scheduler import pick_interaction_pair, choose_interaction

        eligible = [
            s for s in self._engine.sims
            if s.lod_tier in (LODTier.ACTIVE, LODTier.BACKGROUND)
        ]
        if len(eligible) < 2:
            return

        pair = pick_interaction_pair(eligible, self._engine.relationships)
        if not pair:
            return

        sim_a, sim_b = pair
        key = (min(sim_a.sim_id, sim_b.sim_id), max(sim_a.sim_id, sim_b.sim_id))
        last = self._pair_last_interaction.get(key, 0.0)

        # Dynamic interval: social pressure shortens it
        pressure = sim_a.needs.pressure_vector().get("social", 0.0)
        interval = _MIN_INTERACTION_INTERVAL * max(0.3, 1.0 - pressure * 0.6)

        if now - last < interval:
            return

        rel = self._engine.relationships.get(sim_a.sim_id, sim_b.sim_id)
        sim_a._current_venue_name = self._engine._venue.get("name", "")
        interaction = choose_interaction(
            sim_a, sim_b, rel,
            int(now * 1000),           # use wall-time ms as "tick" for cooldowns
            self._engine._datasets,
        )
        self._engine._submit_interaction(sim_a, sim_b, interaction, self._engine._venue)
        self._pair_last_interaction[key] = now

        # Rotate venue occasionally
        if len(self._pair_last_interaction) % 5 == 0:
            import random
            from world.venues import VENUES
            self._engine._venue = {
                **random.choice(VENUES),
                **self._engine._audio_sensor.sense(),
            }

    def _tick_arcs(self, now: float) -> None:
        """Fire arc systems once per _ARC_SIM_HOUR_INTERVAL sim hours."""
        sim_hours_since = self.clock.elapsed_sim_hours_since(self._last_arc_wall)
        if sim_hours_since < _ARC_SIM_HOUR_INTERVAL:
            return
        self._last_arc_wall = now

        from core.arcs import (
            grief_tick, loneliness_tick, burnout_tick,
            should_trigger_burnout, apply_burnout, maybe_generate_dream,
        )
        from core.goals import clear_expired_goal

        pending_ids = {p.sim_a_id for p in self._engine._pending} | \
                      {p.sim_b_id for p in self._engine._pending}

        for sim in list(self._engine.sims):
            tick_proxy = int(now)          # use wall seconds as tick proxy
            sim._current_tick = tick_proxy

            grief_tick(sim)
            loneliness_tick(sim, had_interaction=(sim.sim_id in pending_ids))
            burnout_tick(sim)
            clear_expired_goal(sim, tick_proxy)

            if should_trigger_burnout(sim):
                apply_burnout(sim)
                logger.info("[BURNOUT] %s", sim.name)

            dream = maybe_generate_dream(sim)
            if dream:
                self._engine.memory_store.write(
                    sim.sim_id, sim.sim_id,
                    f"dream:{dream[8:50]}", 0.0, tick=tick_proxy,
                )

            sim.emotion.tick(sim.ocean)

        # Relationship decay every ~24 sim hours
        if sim_hours_since >= 24.0:
            self._engine.relationships.decay_all()

    def _check_aging(self) -> None:
        """Advance age when sim has lived another in-game year."""
        from core.life_stage import (
            advance_age, apply_stage_transition,
            should_die, get_life_stage,
        )

        current_sim_year = int(self.clock.elapsed_sim_years)

        for sim in list(self._engine.sims):
            birth_sim_year = self._sim_birth_years.get(sim.sim_id)
            if birth_sim_year is None:
                self._register_birth(sim)
                birth_sim_year = self._sim_birth_years[sim.sim_id]

            target_age = sim.profile.get("_birth_age", sim.profile.get("age", 25)) + \
                         (current_sim_year - birth_sim_year)
            current_age = sim.profile.get("age", 25)

            while target_age > current_age:
                new_age, old_stage, new_stage = advance_age(sim)
                current_age = new_age
                if old_stage != new_stage:
                    msgs = apply_stage_transition(sim, old_stage, new_stage)
                    for m in msgs:
                        logger.info("[LIFE] %s", m)
                    self._engine._bus.emit(
                        "stage_transition",
                        sim=sim, old_stage=old_stage, new_stage=new_stage,
                        age=new_age, tick=int(time.monotonic()),
                    )
                logger.info(
                    "[AGE] %s is now %d (%s) — %s",
                    sim.name, new_age, new_stage, self.clock.label(),
                )

            if should_die(sim) and not getattr(sim, "_death_queued", False):
                sim._death_queued = True
                self._engine._queue_death(sim)

        self._engine._process_deaths()

    def _register_birth(self, sim) -> None:
        """Record the sim-year offset so age advances continuously."""
        current_sim_year = int(self.clock.elapsed_sim_years)
        birth_age = sim.profile.get("age", 25)
        self._sim_birth_years[sim.sim_id] = current_sim_year - birth_age
        if "_birth_age" not in sim.profile:
            sim.profile["_birth_age"] = birth_age
