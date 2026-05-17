"""
world/social_events.py — Scheduled multi-sim social events.

Event types:
  birthday_party   — auto-triggered on aging tick
  dinner_party     — high-agreeableness sims host these spontaneously
  wedding          — triggered by marriage mechanic
  club_meetup      — promoted from ClubManager (already handled there)

At the scheduled tick, all available guests interact in a group wave.
A success score is computed from average interaction valence.
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine

EVENT_PLANNING_WINDOW   = 5    # ticks in advance events are "scheduled"
DINNER_PARTY_COOLDOWN   = 30   # min ticks between a sim's dinner parties
DINNER_PARTY_AGREEABLENESS = 0.65  # agreeableness threshold to host


@dataclass
class SocialEvent:
    event_id: str
    event_type: str          # "birthday_party", "dinner_party", "wedding"
    host_sim_id: str
    guest_ids: list[str]
    venue: str
    scheduled_tick: int
    completed: bool = False
    success_score: float = 0.0
    outcome_notes: list[str] = field(default_factory=list)


class SocialEventManager:
    def __init__(self) -> None:
        self._events: list[SocialEvent] = []
        self._last_party: dict[str, int] = {}  # sim_id → last party tick

    # ── Scheduling ────────────────────────────────────────────────────────────

    def schedule_birthday(self, sim: "Sim", current_tick: int, guests: list[str]) -> None:
        evt = SocialEvent(
            event_id=uuid.uuid4().hex[:8],
            event_type="birthday_party",
            host_sim_id=sim.sim_id,
            guest_ids=guests,
            venue="home (1:1)",
            scheduled_tick=current_tick + EVENT_PLANNING_WINDOW,
        )
        self._events.append(evt)

    def schedule_dinner(self, host: "Sim", current_tick: int, guests: list[str]) -> None:
        self._events.append(SocialEvent(
            event_id=uuid.uuid4().hex[:8],
            event_type="dinner_party",
            host_sim_id=host.sim_id,
            guest_ids=guests[:5],
            venue="home (1:1)",
            scheduled_tick=current_tick + EVENT_PLANNING_WINDOW,
        ))
        self._last_party[host.sim_id] = current_tick

    def schedule_wedding(
        self, sim_a: "Sim", sim_b: "Sim", current_tick: int, guests: list[str]
    ) -> None:
        self._events.append(SocialEvent(
            event_id=uuid.uuid4().hex[:8],
            event_type="wedding",
            host_sim_id=sim_a.sim_id,
            guest_ids=[sim_b.sim_id] + guests[:8],
            venue="park",
            scheduled_tick=current_tick + EVENT_PLANNING_WINDOW,
        ))

    def maybe_schedule_dinner(self, engine: "SimEngine") -> None:
        """Let agreeable sims spontaneously throw dinner parties."""
        tick = engine.tick_count
        for sim in engine.sims:
            if sim.ocean.get("agreeableness", 0) < DINNER_PARTY_AGREEABLENESS:
                continue
            last = self._last_party.get(sim.sim_id, -DINNER_PARTY_COOLDOWN)
            if tick - last < DINNER_PARTY_COOLDOWN:
                continue
            if random.random() > 0.05:   # 5% chance per eligible sim per call
                continue
            # Invite closest friends
            friends = sorted(
                [o for o in engine.sims if o.sim_id != sim.sim_id],
                key=lambda o: engine.relationships.get(sim.sim_id, o.sim_id).friendship,
                reverse=True,
            )[:5]
            if len(friends) < 2:
                continue
            self.schedule_dinner(sim, tick, [f.sim_id for f in friends])

    # ── Running events ────────────────────────────────────────────────────────

    def tick(self, engine: "SimEngine") -> None:
        """Fire any events whose scheduled_tick has arrived."""
        tick = engine.tick_count
        self.maybe_schedule_dinner(engine)
        for evt in self._events:
            if evt.completed or tick < evt.scheduled_tick:
                continue
            self._run_event(evt, engine)

        # Purge old completed events
        self._events = [e for e in self._events if not e.completed or
                        tick - e.scheduled_tick < 50]

    def _run_event(self, evt: SocialEvent, engine: "SimEngine") -> None:
        evt.completed = True

        host = engine._sim_lookup.get(evt.host_sim_id)
        guests = [
            engine._sim_lookup[gid]
            for gid in evt.guest_ids
            if gid in engine._sim_lookup
            and not getattr(engine._sim_lookup[gid], "_sleeping", False)
        ]
        present = ([host] if host else []) + guests
        if len(present) < 2:
            evt.success_score = 0.3
            return

        # Venue lookup
        from world.venues import VENUES
        venue = next(
            (v for v in VENUES if v.get("name", "") == evt.venue),
            engine._venue,
        )

        # Each pair of present sims gets a social need boost
        for sim in present:
            sim.needs.restore("social", 8.0)
            sim.needs.restore("fun",    5.0)

        # Queue up to 2 interactions between random guest pairs
        from engine.scheduler import pick_interaction_pair, choose_interaction
        attempted = 0
        shuffled  = list(present)
        random.shuffle(shuffled)
        for i in range(0, len(shuffled) - 1, 2):
            if attempted >= 2:
                break
            sim_a, sim_b = shuffled[i], shuffled[i + 1]
            rel = engine.relationships.get(sim_a.sim_id, sim_b.sim_id)
            sim_a._current_venue_name = evt.venue
            interaction = choose_interaction(
                sim_a, sim_b, rel, engine.tick_count, engine._datasets
            )
            engine._submit_interaction(sim_a, sim_b, interaction, venue)
            attempted += 1

        # Emit event
        engine._bus.emit(
            "social_event",
            event_type=evt.event_type,
            host=host.name if host else "?",
            guest_count=len(present),
            venue=evt.venue,
            tick=engine.tick_count,
        )

    def get_pending(self) -> list[SocialEvent]:
        return [e for e in self._events if not e.completed]
