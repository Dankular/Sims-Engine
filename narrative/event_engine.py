"""
narrative/event_engine.py — Central hub for all life events.

EventEngine.process(event, engine):
  1. Stamp expires_tick
  2. Apply all consequences (moodlets, sentiments, rel deltas, rep, emotions,
     interaction unlocks/blocks, wants, fears, celebrity)
  3. Propagate visibility through the social graph
  4. Record in history + per-sim memory
  5. Emit on EventBus

EventEngine.tick(engine):
  - Run EventTriggerSystem to detect new events each tick
  - Decay expired events from history

EventEngine.get_events_known_by(sim_id):
  - Return list of LifeEvent objects that sim_id is aware of

Visibility propagation:
  PRIVATE   → only involved sims
  WITNESSED → + sims at same venue (approximated: all active sims)
  HOUSEHOLD → + all household members of primary sim
  CLUB      → + members of clubs primary sim belongs to
  PUBLIC    → + everyone; enters gossip graph
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING

from core.event_record import LifeEvent, EventConsequences, Visibility

if TYPE_CHECKING:
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)

_MAX_HISTORY = 500   # cap to avoid unbounded growth


class EventEngine:

    def __init__(self) -> None:
        self._history: list[LifeEvent] = []
        self._by_id: dict[str, LifeEvent] = {}
        # sim_id → sorted list of event_ids this sim knows about
        self._known_by: dict[str, list[str]] = defaultdict(list)

    # ── Public API ─────────────────────────────────────────────────────────────

    def process(self, event: LifeEvent, engine: "SimEngine") -> None:
        """Full pipeline: stamp → apply consequences → propagate → store → emit."""
        # 1. Stamp expiry
        if event.duration_ticks > 0 and event.expires_tick <= 0:
            event.expires_tick = engine.tick_count + event.duration_ticks

        # 2. Propagate visibility (expands known_to)
        self._propagate_visibility(event, engine)

        # 3. Apply consequences to all sims who know about the event
        self._apply_consequences(event, engine)

        # 4. Record
        self._store(event)

        # 5. Bus event
        engine._bus.emit(
            "life_event",
            event_type=event.event_type,
            narrative=event.narrative,
            primary=engine._sim_lookup.get(event.primary_sim_id),
            event_data=event,
            tick=engine.tick_count,
        )

        logger.info(
            "[Event] %s — %s (visibility=%s, known_to=%d sims)",
            event.event_type, event.narrative[:60],
            event.visibility, len(event.known_to),
        )

    def tick(self, engine: "SimEngine") -> None:
        """Detect new triggers and decay expired events."""
        from narrative.event_triggers import EventTriggerSystem
        new_events = EventTriggerSystem.check_all(engine)
        for ev in new_events:
            self.process(ev, engine)

        # Purge expired events from history (keep permanent ones)
        if len(self._history) > _MAX_HISTORY:
            self._history = self._history[-_MAX_HISTORY:]
            self._by_id = {e.event_id: e for e in self._history}

    def get_events_known_by(self, sim_id: str, limit: int = 10) -> list[dict]:
        """Return recent events this sim knows about, newest first."""
        ids = self._known_by.get(sim_id, [])
        events = [self._by_id[eid] for eid in reversed(ids) if eid in self._by_id]
        return [self._event_to_dict(e) for e in events[:limit]]

    def get_recent_public(self, limit: int = 5) -> list[dict]:
        public = [e for e in reversed(self._history) if e.visibility == Visibility.PUBLIC]
        return [self._event_to_dict(e) for e in public[:limit]]

    # ── Visibility propagation ─────────────────────────────────────────────────

    def _propagate_visibility(self, event: LifeEvent, engine: "SimEngine") -> None:
        sim = engine._sim_lookup.get(event.primary_sim_id)
        if sim is None:
            return

        vis = event.visibility

        if vis == Visibility.PRIVATE:
            # Only involved sims already in known_to — nothing to add
            pass

        elif vis == Visibility.WITNESSED:
            # Active sims "present" at the same venue (not sleeping)
            for s in engine.sims:
                if not getattr(s, "_sleeping", False):
                    event.known_to.add(s.sim_id)

        elif vis == Visibility.HOUSEHOLD:
            # All household members of primary sim
            for hh in engine.households:
                if hh.id == sim.household_id:
                    event.known_to.update(hh.member_ids)
                    break
            # Also secondary sims' households
            for sid in event.secondary_sim_ids:
                s2 = engine._sim_lookup.get(sid)
                if s2 and s2.household_id:
                    for hh in engine.households:
                        if hh.id == s2.household_id:
                            event.known_to.update(hh.member_ids)
                            break

        elif vis == Visibility.CLUB:
            # All club members of primary sim
            try:
                for club in engine.clubs.get_clubs_for_sim(event.primary_sim_id):
                    event.known_to.update(club.member_ids)
            except Exception:
                pass
            # Fall through to also include household
            for hh in engine.households:
                if hh.id == sim.household_id:
                    event.known_to.update(hh.member_ids)
                    break

        elif vis == Visibility.PUBLIC:
            # Everyone knows
            event.known_to.update(s.sim_id for s in engine.sims)
            # Enter gossip graph
            self._spread_as_gossip(event, engine)

    def _spread_as_gossip(self, event: LifeEvent, engine: "SimEngine") -> None:
        """Push a public event into the gossip graph."""
        gossip_tag = f"[{event.event_type.upper()}] {event.narrative[:80]}"
        # Primary sim's reputation event spreads to all who know them
        for s in engine.sims:
            if s.sim_id != event.primary_sim_id:
                engine.gossip.learn(s.sim_id, event.primary_sim_id, gossip_tag)

    # ── Consequence application ────────────────────────────────────────────────

    def _apply_consequences(self, event: LifeEvent, engine: "SimEngine") -> None:
        c = event.consequences

        # Moodlets
        for sim_id, moodlet_key in c.moodlets:
            s = engine._sim_lookup.get(sim_id)
            if s and hasattr(s, "moodlets"):
                s.moodlets.add(moodlet_key, source=event.event_type)

        # Emotions
        for sim_id, emotion, intensity, duration in c.emotions:
            s = engine._sim_lookup.get(sim_id)
            if s:
                s.emotion.add(emotion, intensity, duration=duration, source=event.event_type)

        # Sentiments
        for sim_a_id, sim_b_id, sentiment_name in c.sentiments:
            rel = engine.relationships.get(sim_a_id, sim_b_id)
            from core.sentiments import add_sentiment
            add_sentiment(rel, sentiment_name, engine.tick_count, source=event.event_type)

        # Relationship deltas
        for sim_a_id, sim_b_id, fd, rd in c.relationship_deltas:
            rel = engine.relationships.get(sim_a_id, sim_b_id)
            rel.apply_deltas(fd, rd)

        # Reputation deltas
        for sim_id, delta in c.reputation_deltas:
            s = engine._sim_lookup.get(sim_id)
            if s:
                s.reputation_score = max(-100, min(100, s.reputation_score + delta))

        # Celebrity deltas
        for sim_id, delta in c.celebrity_deltas:
            s = engine._sim_lookup.get(sim_id)
            if s:
                s.celebrity_score = max(0, min(100, s.celebrity_score + delta))

        # Wants
        from sim_types.sim_types import Want
        for sim_id, desc in c.wants:
            s = engine._sim_lookup.get(sim_id)
            if s:
                new_want = Want(description=desc, target_sim=None,
                                need_linked="social", priority=0.7)
                if desc not in [w.description for w in s.active_wants]:
                    s.active_wants.insert(0, new_want)

        # Fears
        from sim_types.sim_types import Fear
        for sim_id, fear_label in c.fears:
            s = engine._sim_lookup.get(sim_id)
            if s:
                if fear_label not in [f.label for f in s.fears]:
                    s.fears.append(Fear(label=fear_label, severity=0.6))

        # Interaction unlocks
        for sim_id, interaction in c.interactions_unlocked:
            s = engine._sim_lookup.get(sim_id)
            if s:
                if not hasattr(s, "_unlocked_interactions"):
                    s._unlocked_interactions = []
                if interaction not in s._unlocked_interactions:
                    s._unlocked_interactions.append(interaction)

        # Interaction blocks
        for sim_id, interaction in c.interactions_blocked:
            s = engine._sim_lookup.get(sim_id)
            if s:
                if not hasattr(s, "_blocked_interactions"):
                    s._blocked_interactions = []
                if interaction not in s._blocked_interactions:
                    s._blocked_interactions.append(interaction)

    # ── Storage ────────────────────────────────────────────────────────────────

    def _store(self, event: LifeEvent) -> None:
        self._history.append(event)
        self._by_id[event.event_id] = event
        for sim_id in event.known_to:
            self._known_by[sim_id].append(event.event_id)

    # ── Serialisation ──────────────────────────────────────────────────────────

    @staticmethod
    def _event_to_dict(e: LifeEvent) -> dict:
        return {
            "event_id":    e.event_id,
            "event_type":  e.event_type,
            "narrative":   e.narrative,
            "tick":        e.tick,
            "visibility":  e.visibility,
            "valence":     e.valence,
            "intensity":   e.intensity,
        }
