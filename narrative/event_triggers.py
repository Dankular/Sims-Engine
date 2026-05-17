"""
narrative/event_triggers.py — All 6 trigger type detectors.

EventTriggerSystem.check_all(engine) runs every tick and returns a list
of newly-detected LifeEvents for EventEngine to process.

Trigger types:
  1. need_based        — critical need thresholds (health_scare, financial_crisis)
  2. relationship_based — relationship milestone crossings (breakup risk, scandal)
  3. reputation_based  — rep threshold events (redemption arc, villain arc)
  4. calendar_based    — birthdays, anniversaries, holidays (calendar.py already fires these)
  5. random_drama      — probabilistic drama: arguments, rumours, misunderstandings
  6. llm_suggested     — parsed from adjudicator result (handled in _apply_resolved)

Cooldowns prevent the same trigger firing every tick.
"""
from __future__ import annotations

import random
import logging
from typing import TYPE_CHECKING

from core.event_record import LifeEvent, EventType, Visibility
from narrative.event_templates import build_consequences

if TYPE_CHECKING:
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)

# Cooldown: minimum ticks between the same trigger type firing for the same sim
_COOLDOWNS: dict[str, int] = {
    "need_based":          10,
    "relationship_based":  20,
    "reputation_based":    30,
    "random_drama":        15,
}

# Per-sim per-trigger-type last-fired tick
_last_fired: dict[str, dict[str, int]] = {}


def _can_fire(sim_id: str, trigger_type: str, tick: int) -> bool:
    last = _last_fired.get(sim_id, {}).get(trigger_type, -999)
    return tick - last >= _COOLDOWNS.get(trigger_type, 10)


def _record_fire(sim_id: str, trigger_type: str, tick: int) -> None:
    _last_fired.setdefault(sim_id, {})[trigger_type] = tick


class EventTriggerSystem:

    @staticmethod
    def check_all(engine: "SimEngine") -> list[LifeEvent]:
        events: list[LifeEvent] = []
        tick = engine.tick_count

        events += _check_need_based(engine, tick)
        events += _check_relationship_based(engine, tick)
        events += _check_reputation_based(engine, tick)
        events += _check_random_drama(engine, tick)

        return events

    @staticmethod
    def from_llm_suggestion(
        primary_sim_id: str,
        secondary_sim_id: str,
        suggested: dict,
        engine: "SimEngine",
    ) -> LifeEvent | None:
        """
        Build a LifeEvent from an LLM-suggested event field in the adjudicator result.
        Expected shape: {"type": "job_loss", "narrative": "...", "visibility": "household"}
        """
        event_type = suggested.get("type", "")
        narrative  = suggested.get("narrative", "")
        visibility = suggested.get("visibility", Visibility.PRIVATE)

        if not event_type or not narrative:
            return None

        # Validate event type
        valid_types = {
            v for k, v in vars(EventType).items() if not k.startswith("_")
        }
        if event_type not in valid_types:
            return None

        secondary_ids = [secondary_sim_id] if secondary_sim_id else []
        consequences  = build_consequences(event_type, primary_sim_id, secondary_ids, engine,
                                           extra={"source": "llm"})
        return LifeEvent.make(
            event_type     = event_type,
            primary_sim_id = primary_sim_id,
            secondary_sim_ids = secondary_ids,
            narrative      = narrative,
            tick           = engine.tick_count,
            visibility     = visibility,
            valence        = suggested.get("valence", 0.0),
            intensity      = suggested.get("intensity", 0.5),
            consequences   = consequences,
            source         = "llm_suggested",
        )


# ── 1. Need-based triggers ─────────────────────────────────────────────────────

def _check_need_based(engine: "SimEngine", tick: int) -> list[LifeEvent]:
    events = []
    for sim in engine.sims:
        if not _can_fire(sim.sim_id, "need_based", tick):
            continue

        # Health scare — critically low energy for many ticks
        if getattr(sim, "_low_energy_ticks", 0) >= 5:
            _record_fire(sim.sim_id, "need_based", tick)
            c = build_consequences(EventType.HEALTH_SCARE, sim.sim_id, [], engine)
            events.append(LifeEvent.make(
                EventType.HEALTH_SCARE, sim.sim_id,
                f"{sim.name} is exhausted and showing signs of a health scare.",
                tick, visibility=Visibility.HOUSEHOLD,
                valence=-0.6, intensity=0.7, duration_ticks=15,
                consequences=c, source="trigger:need_based",
            ))
            continue

        # Financial crisis — simoleons critically low
        if sim.simoleons < 50 and random.random() < 0.3:
            _record_fire(sim.sim_id, "need_based", tick)
            c = build_consequences(EventType.JOB_LOSS, sim.sim_id, [], engine,
                                   extra={"cause": "financial_crisis"})
            events.append(LifeEvent.make(
                EventType.JOB_LOSS, sim.sim_id,
                f"{sim.name} is in financial crisis — nearly out of money.",
                tick, visibility=Visibility.HOUSEHOLD,
                valence=-0.7, intensity=0.8, duration_ticks=20,
                consequences=c, source="trigger:need_based",
            ))

    return events


# ── 2. Relationship-based triggers ────────────────────────────────────────────

def _check_relationship_based(engine: "SimEngine", tick: int) -> list[LifeEvent]:
    events = []
    processed_pairs: set[frozenset] = set()

    for sim in engine.sims:
        if not _can_fire(sim.sim_id, "relationship_based", tick):
            continue

        for other in engine.sims:
            if other.sim_id == sim.sim_id:
                continue
            pair = frozenset({sim.sim_id, other.sim_id})
            if pair in processed_pairs:
                continue

            rel = engine.relationships.get(sim.sim_id, other.sim_id)

            # Breakup risk — romance was high but tanked
            if (
                rel.romance < 10
                and rel.interactions > 5
                and rel.romance < -5
                and random.random() < 0.15
            ):
                processed_pairs.add(pair)
                _record_fire(sim.sim_id, "relationship_based", tick)
                c = build_consequences(EventType.BREAKUP, sim.sim_id, [other.sim_id], engine)
                events.append(LifeEvent.make(
                    EventType.BREAKUP, sim.sim_id,
                    f"{sim.name} and {other.name} have grown apart — things ended badly.",
                    tick,
                    secondary_sim_ids=[other.sim_id],
                    visibility=Visibility.HOUSEHOLD,
                    valence=-0.6, intensity=0.7, duration_ticks=25,
                    consequences=c, source="trigger:relationship_based",
                ))

            # Rivalry escalation — very negative friendship
            elif (
                rel.friendship <= -55
                and rel.interactions > 8
                and random.random() < 0.10
            ):
                processed_pairs.add(pair)
                _record_fire(sim.sim_id, "relationship_based", tick)
                c = build_consequences(EventType.SCANDAL, sim.sim_id, [other.sim_id], engine,
                                       extra={"rep_hit": -8.0})
                c.sentiments.append((sim.sim_id,   other.sim_id, "rivalry_formed"))
                c.sentiments.append((other.sim_id, sim.sim_id,   "rivalry_formed"))
                events.append(LifeEvent.make(
                    EventType.RIVALRY, sim.sim_id,
                    f"{sim.name} and {other.name}'s conflict has become a full rivalry.",
                    tick,
                    secondary_sim_ids=[other.sim_id],
                    visibility=Visibility.CLUB,
                    valence=-0.5, intensity=0.6, duration_ticks=40,
                    consequences=c, source="trigger:relationship_based",
                ))

    return events


# ── 3. Reputation-based triggers ──────────────────────────────────────────────

def _check_reputation_based(engine: "SimEngine", tick: int) -> list[LifeEvent]:
    events = []
    for sim in engine.sims:
        if not _can_fire(sim.sim_id, "reputation_based", tick):
            continue

        rep = sim.reputation_score

        # Redemption arc — reputation recently climbed above threshold
        if rep >= 60 and random.random() < 0.08:
            _record_fire(sim.sim_id, "reputation_based", tick)
            c = build_consequences(EventType.REDEMPTION, sim.sim_id, [], engine)
            events.append(LifeEvent.make(
                EventType.REDEMPTION, sim.sim_id,
                f"{sim.name}'s reputation has fully recovered — the community respects them again.",
                tick, visibility=Visibility.PUBLIC,
                valence=+0.8, intensity=0.7, duration_ticks=30,
                consequences=c, source="trigger:reputation_based",
            ))

        # Scandal threshold — rep dropped very low
        elif rep <= -55 and random.random() < 0.12:
            _record_fire(sim.sim_id, "reputation_based", tick)
            witnesses = [
                o.sim_id for o in engine.sims
                if o.sim_id != sim.sim_id
                and engine.relationships.get(sim.sim_id, o.sim_id).friendship >= 20
            ][:4]
            c = build_consequences(EventType.SCANDAL, sim.sim_id, witnesses, engine,
                                   extra={"rep_hit": -10.0})
            events.append(LifeEvent.make(
                EventType.SCANDAL, sim.sim_id,
                f"{sim.name}'s reputation hits rock bottom — a scandal erupts.",
                tick,
                secondary_sim_ids=witnesses,
                visibility=Visibility.PUBLIC,
                valence=-0.8, intensity=0.9, duration_ticks=40,
                consequences=c, source="trigger:reputation_based",
            ))

    return events


# ── 5. Random drama ────────────────────────────────────────────────────────────

_DRAMA_TYPES = [
    # (weight, drama_type, visibility, valence, narrative_fn)
    (4, "argument",         Visibility.WITNESSED, -0.4,
     lambda a, b: f"{a} and {b} had a heated argument that others witnessed."),
    (3, "misunderstanding", Visibility.HOUSEHOLD, -0.3,
     lambda a, b: f"{a} misunderstood something {b} said and took it badly."),
    (2, "rumour_spread",    Visibility.CLUB,      -0.5,
     lambda a, b: f"An unflattering rumour about {a} spread through their social circle."),
    (1, "public_meltdown",  Visibility.PUBLIC,    -0.7,
     lambda a, b: f"{a} had a very public emotional meltdown — everyone noticed."),
]

_DRAMA_WEIGHTS = [d[0] for d in _DRAMA_TYPES]


def _check_random_drama(engine: "SimEngine", tick: int) -> list[LifeEvent]:
    events = []
    active = [s for s in engine.sims
              if not getattr(s, "_sleeping", False)
              and s.reputation_score > -80]

    if len(active) < 2:
        return events

    # ~8% chance of drama per tick across the whole population
    if random.random() > 0.08:
        return events

    sim = random.choice(active)
    if not _can_fire(sim.sim_id, "random_drama", tick):
        return events

    others = [s for s in active if s.sim_id != sim.sim_id]
    if not others:
        return events
    other = random.choice(others)

    _, drama_type, visibility, valence, narrative_fn = random.choices(
        _DRAMA_TYPES, weights=_DRAMA_WEIGHTS, k=1
    )[0]

    _record_fire(sim.sim_id, "random_drama", tick)
    narrative = narrative_fn(sim.name, other.name)
    c = build_consequences(EventType.RANDOM_DRAMA, sim.sim_id, [other.sim_id], engine,
                           extra={"drama_type": drama_type})
    events.append(LifeEvent.make(
        EventType.RANDOM_DRAMA, sim.sim_id,
        narrative, tick,
        secondary_sim_ids=[other.sim_id],
        visibility=visibility,
        valence=valence, intensity=0.5, duration_ticks=12,
        consequences=c, source="trigger:random_drama",
    ))
    return events
