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
        events += _check_romance_arc(engine, tick)
        events += _check_friendship_arc(engine, tick)
        events += _check_family_arc(engine, tick)
        events += _check_aging_arc(engine, tick)
        events += _check_career_depth(engine, tick)
        events += _check_education(engine, tick)
        events += _check_health_depth(engine, tick)
        events += _check_gossip_rumour(engine, tick)
        events += _check_world_context(engine, tick)
        events += _check_community(engine, tick)
        events += _check_household(engine, tick)
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


# ── Romance arc triggers ───────────────────────────────────────────────────────

_ROMANCE_COOLDOWNS = {
    "crush":    30,
    "confess":  40,
    "kiss":     50,
    "dating":   60,
    "engage":   80,
}
_romance_last: dict[str, dict[str, int]] = {}


def _romance_can(pair_key: str, stage: str, tick: int) -> bool:
    return tick - _romance_last.get(pair_key, {}).get(stage, -999) >= _ROMANCE_COOLDOWNS[stage]


def _romance_record(pair_key: str, stage: str, tick: int) -> None:
    _romance_last.setdefault(pair_key, {})[stage] = tick


def _check_romance_arc(engine: "SimEngine", tick: int) -> list[LifeEvent]:
    from core.event_record import EventType, Visibility
    events = []
    processed: set[frozenset] = set()

    for sim in engine.sims:
        for other in engine.sims:
            if other.sim_id <= sim.sim_id:
                continue
            pair = frozenset({sim.sim_id, other.sim_id})
            if pair in processed:
                continue
            processed.add(pair)
            pk = f"{min(sim.sim_id,other.sim_id)}_{max(sim.sim_id,other.sim_id)}"
            rel = engine.relationships.get(sim.sim_id, other.sim_id)

            # Crush formed: romance crosses 20 for the first time
            if rel.romance >= 20 and _romance_can(pk, "crush", tick):
                _romance_record(pk, "crush", tick)
                c = build_consequences(EventType.CRUSH_FORMED, sim.sim_id, [other.sim_id], engine)
                events.append(LifeEvent.make(
                    EventType.CRUSH_FORMED, sim.sim_id,
                    f"{sim.name} has developed a crush on {other.name}.",
                    tick, secondary_sim_ids=[other.sim_id],
                    visibility=Visibility.PRIVATE,
                    valence=+0.7, intensity=0.6, duration_ticks=20,
                    consequences=c, source="trigger:romance_arc",
                ))

            # Love confession: romance 40+
            if rel.romance >= 40 and rel.interactions >= 6 and _romance_can(pk, "confess", tick):
                _romance_record(pk, "confess", tick)
                c = build_consequences(EventType.LOVE_CONFESSION, sim.sim_id, [other.sim_id], engine)
                events.append(LifeEvent.make(
                    EventType.LOVE_CONFESSION, sim.sim_id,
                    f"{sim.name} confessed their feelings to {other.name}.",
                    tick, secondary_sim_ids=[other.sim_id],
                    visibility=Visibility.WITNESSED,
                    valence=+0.8, intensity=0.7, duration_ticks=15,
                    consequences=c, source="trigger:romance_arc",
                ))

            # First kiss: romance 55+
            if rel.romance >= 55 and _romance_can(pk, "kiss", tick):
                _romance_record(pk, "kiss", tick)
                c = build_consequences(EventType.FIRST_KISS_EVENT, sim.sim_id, [other.sim_id], engine)
                events.append(LifeEvent.make(
                    EventType.FIRST_KISS_EVENT, sim.sim_id,
                    f"{sim.name} and {other.name} shared their first kiss.",
                    tick, secondary_sim_ids=[other.sim_id],
                    visibility=Visibility.WITNESSED,
                    valence=+0.9, intensity=0.8, duration_ticks=20,
                    consequences=c, source="trigger:romance_arc",
                ))

            # Dating started: romance 65+
            if rel.romance >= 65 and _romance_can(pk, "dating", tick):
                _romance_record(pk, "dating", tick)
                c = build_consequences(EventType.DATING_STARTED, sim.sim_id, [other.sim_id], engine)
                events.append(LifeEvent.make(
                    EventType.DATING_STARTED, sim.sim_id,
                    f"{sim.name} and {other.name} are now officially dating.",
                    tick, secondary_sim_ids=[other.sim_id],
                    visibility=Visibility.HOUSEHOLD,
                    valence=+0.8, intensity=0.7, duration_ticks=25,
                    consequences=c, source="trigger:romance_arc",
                ))

            # Engagement: romance 85+ without marriage yet
            if (
                rel.romance >= 85
                and not getattr(sim, "_married_to", None)
                and not getattr(other, "_married_to", None)
                and _romance_can(pk, "engage", tick)
                and random.random() < 0.15
            ):
                _romance_record(pk, "engage", tick)
                c = build_consequences(EventType.ENGAGEMENT, sim.sim_id, [other.sim_id], engine)
                events.append(LifeEvent.make(
                    EventType.ENGAGEMENT, sim.sim_id,
                    f"{sim.name} proposed to {other.name} — they said yes!",
                    tick, secondary_sim_ids=[other.sim_id],
                    visibility=Visibility.PUBLIC,
                    valence=+1.0, intensity=0.95, duration_ticks=30,
                    consequences=c, source="trigger:romance_arc",
                ))

    return events


# ── Friendship arc triggers ────────────────────────────────────────────────────

_friend_milestones: dict[str, set[str]] = {}  # sim_id → set of "best_friend:other_id" etc.


def _check_friendship_arc(engine: "SimEngine", tick: int) -> list[LifeEvent]:
    from core.event_record import EventType, Visibility
    events = []

    for sim in engine.sims:
        for other in engine.sims:
            if other.sim_id <= sim.sim_id:
                continue
            rel = engine.relationships.get(sim.sim_id, other.sim_id)
            mkey = f"{min(sim.sim_id,other.sim_id)}_bf_{max(sim.sim_id,other.sim_id)}"
            dkey = f"{min(sim.sim_id,other.sim_id)}_da_{max(sim.sim_id,other.sim_id)}"

            # Best friends formed
            if rel.friendship >= 80 and mkey not in _friend_milestones.get(sim.sim_id, set()):
                _friend_milestones.setdefault(sim.sim_id, set()).add(mkey)
                _friend_milestones.setdefault(other.sim_id, set()).add(mkey)
                c = build_consequences(EventType.BEST_FRIENDS_FORMED, sim.sim_id, [other.sim_id], engine)
                events.append(LifeEvent.make(
                    EventType.BEST_FRIENDS_FORMED, sim.sim_id,
                    f"{sim.name} and {other.name} have become best friends.",
                    tick, secondary_sim_ids=[other.sim_id],
                    visibility=Visibility.HOUSEHOLD,
                    valence=+0.9, intensity=0.8, duration_ticks=40,
                    consequences=c, source="trigger:friendship_arc",
                ))

            # Drifting apart: was close, now decayed
            if (
                rel.friendship < 30
                and rel.interactions > 10
                and dkey not in _friend_milestones.get(sim.sim_id, set())
                and random.random() < 0.05
            ):
                _friend_milestones.setdefault(sim.sim_id, set()).add(dkey)
                c = build_consequences(EventType.DRIFTING_APART, sim.sim_id, [other.sim_id], engine)
                events.append(LifeEvent.make(
                    EventType.DRIFTING_APART, sim.sim_id,
                    f"{sim.name} and {other.name} have grown apart over time.",
                    tick, secondary_sim_ids=[other.sim_id],
                    visibility=Visibility.PRIVATE,
                    valence=-0.4, intensity=0.5, duration_ticks=20,
                    consequences=c, source="trigger:friendship_arc",
                ))

            # Jealousy incident: jealousy_score spikes
            if rel.jealousy_score >= 65 and _can_fire(sim.sim_id, "relationship_based", tick):
                _record_fire(sim.sim_id, "relationship_based", tick)
                c = build_consequences(EventType.JEALOUSY_INCIDENT, sim.sim_id, [other.sim_id], engine)
                events.append(LifeEvent.make(
                    EventType.JEALOUSY_INCIDENT, sim.sim_id,
                    f"{sim.name} is overwhelmed with jealousy toward {other.name}.",
                    tick, secondary_sim_ids=[other.sim_id],
                    visibility=Visibility.WITNESSED,
                    valence=-0.5, intensity=0.6, duration_ticks=10,
                    consequences=c, source="trigger:friendship_arc",
                ))

    return events


# ── Family arc triggers ────────────────────────────────────────────────────────

def _check_family_arc(engine: "SimEngine", tick: int) -> list[LifeEvent]:
    from core.event_record import EventType, Visibility
    events = []

    for sim in engine.sims:
        if not _can_fire(sim.sim_id, "relationship_based", tick):
            continue

        # Sibling rivalry: two children in same household with low friendship
        if sim.is_child_of:
            siblings = [
                o for o in engine.sims
                if o.sim_id != sim.sim_id
                and o.is_child_of
                and o.household_id == sim.household_id
                and engine.relationships.get(sim.sim_id, o.sim_id).friendship < -10
            ]
            if siblings and random.random() < 0.08:
                _record_fire(sim.sim_id, "relationship_based", tick)
                sib = siblings[0]
                c = build_consequences(EventType.SIBLING_RIVALRY, sim.sim_id, [sib.sim_id], engine)
                events.append(LifeEvent.make(
                    EventType.SIBLING_RIVALRY, sim.sim_id,
                    f"{sim.name} and {sib.name} are caught in ongoing sibling rivalry.",
                    tick, secondary_sim_ids=[sib.sim_id],
                    visibility=Visibility.HOUSEHOLD,
                    valence=-0.5, intensity=0.6, duration_ticks=15,
                    consequences=c, source="trigger:family_arc",
                ))

        # Family feud: household members at enemy level
        hh_members = [
            o for o in engine.sims
            if o.sim_id != sim.sim_id
            and o.household_id == sim.household_id
            and engine.relationships.get(sim.sim_id, o.sim_id).friendship <= -50
        ]
        if hh_members and random.random() < 0.06:
            _record_fire(sim.sim_id, "relationship_based", tick)
            other = hh_members[0]
            c = build_consequences(EventType.FAMILY_FEUD, sim.sim_id, [other.sim_id], engine)
            events.append(LifeEvent.make(
                EventType.FAMILY_FEUD, sim.sim_id,
                f"A family feud erupts between {sim.name} and {other.name}.",
                tick, secondary_sim_ids=[other.sim_id],
                visibility=Visibility.HOUSEHOLD,
                valence=-0.7, intensity=0.8, duration_ticks=30,
                consequences=c, source="trigger:family_arc",
            ))

    return events


# ── Gossip / rumour lifecycle triggers ─────────────────────────────────────────

_rumour_pool: list[dict] = []  # {subject_id, text, credibility, believers, tick_created}
_rumour_cooldown: dict[str, int] = {}


def _check_gossip_rumour(engine: "SimEngine", tick: int) -> list[LifeEvent]:
    from core.event_record import EventType, Visibility
    events = []

    # Occasionally a sim invents/exaggerates a rumour about another
    if random.random() < 0.06 and len(engine.sims) >= 3:
        creator = random.choice(engine.sims)
        subjects = [s for s in engine.sims if s.sim_id != creator.sim_id]
        if subjects:
            subject = random.choice(subjects)
            if tick - _rumour_cooldown.get(creator.sim_id, -50) >= 30:
                _rumour_cooldown[creator.sim_id] = tick
                rep = subject.reputation_score
                rumour_text = (
                    f"{subject.name} reportedly did something scandalous"
                    if rep < 0 else
                    f"There are whispers that {subject.name} is hiding something"
                )
                _rumour_pool.append({
                    "subject_id": subject.sim_id,
                    "text":       rumour_text,
                    "credibility": max(0.1, 0.3 + creator.celebrity_score / 200),
                    "believers":   {creator.sim_id},
                    "tick":        tick,
                })
                c = build_consequences(EventType.RUMOUR_CREATED, creator.sim_id, [subject.sim_id], engine)
                events.append(LifeEvent.make(
                    EventType.RUMOUR_CREATED, creator.sim_id,
                    f"{creator.name} started a rumour: \"{rumour_text[:60]}\"",
                    tick, secondary_sim_ids=[subject.sim_id],
                    visibility=Visibility.CLUB,
                    valence=-0.3, intensity=0.4, duration_ticks=20,
                    consequences=c, source="trigger:gossip",
                ))

    # Spread existing rumours
    for rumour in list(_rumour_pool):
        if tick - rumour["tick"] > 40:
            _rumour_pool.remove(rumour)
            continue

        # Each tick: 20% chance a new sim hears it
        if random.random() < 0.20:
            potential = [s for s in engine.sims if s.sim_id not in rumour["believers"]]
            if potential:
                hearer = random.choice(potential)
                rumour["believers"].add(hearer.sim_id)

                # Believes it? credibility × agreeableness of hearer
                believe_chance = rumour["credibility"] * (1.0 - hearer.ocean.get("openness", 0.5) * 0.3)
                if random.random() < believe_chance:
                    subject = engine._sim_lookup.get(rumour["subject_id"])
                    if subject:
                        c = build_consequences(
                            EventType.RUMOUR_BELIEVED, hearer.sim_id,
                            [rumour["subject_id"]], engine,
                            extra={"subject_id": rumour["subject_id"],
                                   "rep_hit": -6.0 * rumour["credibility"]},
                        )
                        events.append(LifeEvent.make(
                            EventType.RUMOUR_BELIEVED, hearer.sim_id,
                            f"{hearer.name} believes a rumour about {subject.name}.",
                            tick, secondary_sim_ids=[rumour["subject_id"]],
                            visibility=Visibility.PRIVATE,
                            valence=-0.4, intensity=0.5, duration_ticks=15,
                            consequences=c, source="trigger:gossip",
                        ))

    return events


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


# ── Aging arc triggers ─────────────────────────────────────────────────────────

_aging_fired: dict[str, set[str]] = {}  # sim_id -> set of fired stage keys


def _check_aging_arc(engine, tick):
    from core.event_record import EventType, Visibility
    events = []
    for sim in engine.sims:
        age = sim.profile.get("age", 25)
        fired = _aging_fired.setdefault(sim.sim_id, set())

        stage = "child" if age < 13 else "teen" if age < 18 else "adult" if age < 65 else "elder"
        skey = f"stage:{stage}"
        if skey not in fired:
            fired.add(skey)
            c = build_consequences(EventType.LIFE_STAGE_TRANSITION, sim.sim_id, [], engine,
                                   extra={"new_stage": stage})
            events.append(LifeEvent.make(
                EventType.LIFE_STAGE_TRANSITION, sim.sim_id,
                f"{sim.name} has entered the {stage} life stage.",
                tick, visibility=Visibility.HOUSEHOLD,
                valence=+0.6, intensity=0.7, duration_ticks=20,
                consequences=c, source="trigger:aging",
            ))

        if 40 <= age <= 55 and sim.career_performance < 45 and "midlife_crisis" not in fired:
            if random.random() < 0.08:
                fired.add("midlife_crisis")
                c = build_consequences(EventType.MIDLIFE_CRISIS, sim.sim_id, [], engine)
                events.append(LifeEvent.make(
                    EventType.MIDLIFE_CRISIS, sim.sim_id,
                    f"{sim.name} is experiencing a midlife crisis.",
                    tick, visibility=Visibility.HOUSEHOLD,
                    valence=-0.4, intensity=0.7, duration_ticks=25,
                    consequences=c, source="trigger:aging",
                ))

        if age >= 65 and tick % 20 == 0 and _can_fire(sim.sim_id, "need_based", tick):
            _record_fire(sim.sim_id, "need_based", tick)
            c = build_consequences(EventType.ELDER_DECLINE, sim.sim_id, [], engine)
            events.append(LifeEvent.make(
                EventType.ELDER_DECLINE, sim.sim_id,
                f"{sim.name} shows signs of age-related decline.",
                tick, visibility=Visibility.HOUSEHOLD,
                valence=-0.3, intensity=0.5, duration_ticks=15,
                consequences=c, source="trigger:aging",
            ))

        if age >= 72 and "death_prep" not in fired and random.random() < 0.04:
            fired.add("death_prep")
            c = build_consequences(EventType.DEATH_PREPARATION, sim.sim_id, [], engine)
            events.append(LifeEvent.make(
                EventType.DEATH_PREPARATION, sim.sim_id,
                f"{sim.name} begins preparing for the end of life.",
                tick, visibility=Visibility.HOUSEHOLD,
                valence=-0.2, intensity=0.6, duration_ticks=40,
                consequences=c, source="trigger:aging",
            ))

    return events


# ── Career depth triggers ──────────────────────────────────────────────────────

def _check_career_depth(engine, tick):
    from core.event_record import EventType, Visibility
    events = []
    for sim in engine.sims:
        if not _can_fire(sim.sim_id, "need_based", tick):
            continue
        cp = sim.career_performance

        if cp < 30 and cp > 10 and random.random() < 0.08:
            _record_fire(sim.sim_id, "need_based", tick)
            c = build_consequences(EventType.DEMOTION, sim.sim_id, [], engine)
            events.append(LifeEvent.make(
                EventType.DEMOTION, sim.sim_id,
                f"{sim.name} has been demoted due to poor performance.",
                tick, visibility=Visibility.HOUSEHOLD,
                valence=-0.7, intensity=0.75, duration_ticks=20,
                consequences=c, source="trigger:career",
            ))
        elif cp < 10 and random.random() < 0.12:
            _record_fire(sim.sim_id, "need_based", tick)
            c = build_consequences(EventType.FIRED, sim.sim_id, [], engine)
            events.append(LifeEvent.make(
                EventType.FIRED, sim.sim_id,
                f"{sim.name} has been fired from their job.",
                tick, visibility=Visibility.PUBLIC,
                valence=-0.9, intensity=0.9, duration_ticks=25,
                consequences=c, source="trigger:career",
            ))

        for cw_id in sim.coworker_ids:
            cw = engine._sim_lookup.get(cw_id)
            if cw is None:
                continue
            rel = engine.relationships.get(sim.sim_id, cw_id)
            pk = f"wr_{min(sim.sim_id, cw_id)}_{max(sim.sim_id, cw_id)}"
            rk = f"wri_{min(sim.sim_id, cw_id)}_{max(sim.sim_id, cw_id)}"
            f_set = _aging_fired.setdefault(sim.sim_id, set())

            if rel.romance >= 30 and rel.friendship >= 40 and pk not in f_set and random.random() < 0.15:
                f_set.add(pk)
                c = build_consequences(EventType.WORKPLACE_ROMANCE, sim.sim_id, [cw_id], engine)
                events.append(LifeEvent.make(
                    EventType.WORKPLACE_ROMANCE, sim.sim_id,
                    f"{sim.name} and {cw.name} have a workplace romance brewing.",
                    tick, secondary_sim_ids=[cw_id],
                    visibility=Visibility.CLUB,
                    valence=+0.6, intensity=0.6, duration_ticks=20,
                    consequences=c, source="trigger:career",
                ))
            if rel.friendship < -20 and rk not in f_set and random.random() < 0.12:
                f_set.add(rk)
                c = build_consequences(EventType.WORKPLACE_RIVALRY, sim.sim_id, [cw_id], engine)
                events.append(LifeEvent.make(
                    EventType.WORKPLACE_RIVALRY, sim.sim_id,
                    f"{sim.name} and {cw.name} have become workplace rivals.",
                    tick, secondary_sim_ids=[cw_id],
                    visibility=Visibility.CLUB,
                    valence=-0.5, intensity=0.6, duration_ticks=25,
                    consequences=c, source="trigger:career",
                ))

    return events


# ── Education triggers ─────────────────────────────────────────────────────────

_edu_cooldown: dict[str, int] = {}


def _check_education(engine, tick):
    from core.event_record import EventType, Visibility
    events = []
    children = [s for s in engine.sims if s.is_child_of and s.profile.get("age", 25) < 18]

    for sim in children:
        last = _edu_cooldown.get(sim.sim_id, -25)
        if tick - last < 20:
            continue
        logic = sim.skills.levels.get("logic", 0)

        if logic >= 5 and random.random() < 0.10:
            _edu_cooldown[sim.sim_id] = tick
            c = build_consequences(EventType.EXAM_SUCCESS, sim.sim_id, [], engine)
            events.append(LifeEvent.make(
                EventType.EXAM_SUCCESS, sim.sim_id,
                f"{sim.name} passed an exam with flying colours.",
                tick, visibility=Visibility.HOUSEHOLD,
                valence=+0.7, intensity=0.6, duration_ticks=10,
                consequences=c, source="trigger:education",
            ))
        elif logic < 2 and random.random() < 0.08:
            _edu_cooldown[sim.sim_id] = tick
            c = build_consequences(EventType.HOMEWORK_FAILURE, sim.sim_id, [], engine)
            events.append(LifeEvent.make(
                EventType.HOMEWORK_FAILURE, sim.sim_id,
                f"{sim.name} failed to complete their homework.",
                tick, visibility=Visibility.HOUSEHOLD,
                valence=-0.4, intensity=0.4, duration_ticks=8,
                consequences=c, source="trigger:education",
            ))

        skey = f"scholarship_{sim.sim_id}"
        if logic >= 8 and skey not in _aging_fired.get(sim.sim_id, set()):
            _aging_fired.setdefault(sim.sim_id, set()).add(skey)
            c = build_consequences(EventType.SCHOLARSHIP, sim.sim_id, [], engine,
                                   extra={"amount": 2000.0})
            events.append(LifeEvent.make(
                EventType.SCHOLARSHIP, sim.sim_id,
                f"{sim.name} has been awarded a scholarship for academic excellence.",
                tick, visibility=Visibility.PUBLIC,
                valence=+0.9, intensity=0.85, duration_ticks=20,
                consequences=c, source="trigger:education",
            ))

    for sim in engine.sims:
        gkey = f"grad_{sim.sim_id}"
        if (sim.profile.get("age", 0) == 18
                and gkey not in _aging_fired.get(sim.sim_id, set())):
            _aging_fired.setdefault(sim.sim_id, set()).add(gkey)
            c = build_consequences(EventType.GRADUATION, sim.sim_id, [], engine)
            events.append(LifeEvent.make(
                EventType.GRADUATION, sim.sim_id,
                f"{sim.name} has graduated and entered adulthood.",
                tick, visibility=Visibility.CLUB,
                valence=+0.9, intensity=0.85, duration_ticks=20,
                consequences=c, source="trigger:education",
            ))

    return events


# ── Health depth triggers ──────────────────────────────────────────────────────

def _check_health_depth(engine, tick):
    from core.event_record import EventType, Visibility
    events = []
    for sim in engine.sims:
        if getattr(sim, "_sleeping", False):
            continue

        if (getattr(sim, "_high_perf_low_energy_ticks", 0) >= 8
                and _can_fire(sim.sim_id, "need_based", tick)):
            _record_fire(sim.sim_id, "need_based", tick)
            c = build_consequences(EventType.CHRONIC_STRESS, sim.sim_id, [], engine)
            events.append(LifeEvent.make(
                EventType.CHRONIC_STRESS, sim.sim_id,
                f"{sim.name} is suffering from chronic stress.",
                tick, visibility=Visibility.HOUSEHOLD,
                valence=-0.6, intensity=0.7, duration_ticks=15,
                consequences=c, source="trigger:health",
            ))

        if random.random() < 0.005 and _can_fire(sim.sim_id, "need_based", tick):
            _record_fire(sim.sim_id, "need_based", tick)
            severity = random.choice(["minor", "minor", "moderate", "severe"])
            c = build_consequences(EventType.INJURY, sim.sim_id, [], engine,
                                   extra={"severity": severity})
            events.append(LifeEvent.make(
                EventType.INJURY, sim.sim_id,
                f"{sim.name} suffered a {severity} injury.",
                tick, visibility=Visibility.HOUSEHOLD,
                valence=-0.5, intensity=0.6, duration_ticks=12,
                consequences=c, source="trigger:health",
            ))

        if (getattr(sim, "health_status", "healthy") == "sick"
                and getattr(sim, "illness_severity", "mild") == "severe"
                and sim.needs.energy < 15
                and _can_fire(sim.sim_id, "need_based", tick)):
            _record_fire(sim.sim_id, "need_based", tick)
            c = build_consequences(EventType.HOSPITALIZATION, sim.sim_id, [], engine)
            events.append(LifeEvent.make(
                EventType.HOSPITALIZATION, sim.sim_id,
                f"{sim.name} has been hospitalized due to severe illness.",
                tick, visibility=Visibility.PUBLIC,
                valence=-0.8, intensity=0.9, duration_ticks=20,
                consequences=c, source="trigger:health",
            ))

    return events



# ── World / seasonal triggers ──────────────────────────────────────────────────

_seasonal_fired: dict[str, int] = {}   # event_key → last tick fired


def _check_world_context(engine, tick):
    from core.event_record import EventType, Visibility
    events = []

    weather_name = engine.weather.current.name
    w_key = f"weather_{weather_name}"
    last  = _seasonal_fired.get(w_key, -40)
    if tick - last >= 30:
        _seasonal_fired[w_key] = tick

        if weather_name == "heatwave":
            c = build_consequences(EventType.HEATWAVE_EVENT, "", [], engine)
            events.append(LifeEvent.make(
                EventType.HEATWAVE_EVENT, engine.sims[0].sim_id if engine.sims else "",
                "A heatwave hits — everyone is sweltering.",
                tick, visibility=Visibility.PUBLIC,
                valence=-0.4, intensity=0.6, duration_ticks=8,
                consequences=c, source="trigger:weather",
            ))

        elif weather_name == "snowy":
            c = build_consequences(EventType.SNOW_DAY, "", [], engine)
            events.append(LifeEvent.make(
                EventType.SNOW_DAY, engine.sims[0].sim_id if engine.sims else "",
                "Snow blankets the neighbourhood — a spontaneous snow day.",
                tick, visibility=Visibility.PUBLIC,
                valence=+0.5, intensity=0.6, duration_ticks=8,
                consequences=c, source="trigger:weather",
            ))

        elif weather_name == "stormy":
            c = build_consequences(EventType.STORM_EVENT, "", [], engine)
            events.append(LifeEvent.make(
                EventType.STORM_EVENT, engine.sims[0].sim_id if engine.sims else "",
                "A violent storm sweeps through — everyone hunkers down.",
                tick, visibility=Visibility.PUBLIC,
                valence=-0.3, intensity=0.5, duration_ticks=6,
                consequences=c, source="trigger:weather",
            ))

    # Seasonal depression check: cloudy/foggy for extended period
    for sim in engine.sims:
        if weather_name in ("cloudy", "foggy", "rainy"):
            if (tick % 25 == 0
                    and sim.ocean.get("neuroticism", 0.5) > 0.6
                    and _can_fire(sim.sim_id, "need_based", tick)):
                _record_fire(sim.sim_id, "need_based", tick)
                c = build_consequences(EventType.SEASONAL_DEPRESSION, sim.sim_id, [], engine)
                events.append(LifeEvent.make(
                    EventType.SEASONAL_DEPRESSION, sim.sim_id,
                    f"{sim.name} feels the weight of the grey season.",
                    tick, visibility=Visibility.PRIVATE,
                    valence=-0.5, intensity=0.6, duration_ticks=10,
                    consequences=c, source="trigger:seasonal",
                ))

        # Seasonal boost: sunny + high openness
        elif weather_name == "sunny":
            if (tick % 20 == 0
                    and sim.ocean.get("openness", 0.5) > 0.6
                    and _can_fire(sim.sim_id, "need_based", tick)):
                _record_fire(sim.sim_id, "need_based", tick)
                c = build_consequences(EventType.SEASONAL_BOOST, sim.sim_id, [], engine)
                events.append(LifeEvent.make(
                    EventType.SEASONAL_BOOST, sim.sim_id,
                    f"{sim.name} feels energised by the beautiful weather.",
                    tick, visibility=Visibility.PRIVATE,
                    valence=+0.5, intensity=0.4, duration_ticks=8,
                    consequences=c, source="trigger:seasonal",
                ))

    return events


# ── Community triggers ─────────────────────────────────────────────────────────

_community_cooldowns: dict[str, int] = {}


def _check_community(engine, tick):
    from core.event_record import EventType, Visibility
    events = []

    # Festival: every 30 ticks in sunny weather or holiday
    fest_key = "festival"
    if (tick - _community_cooldowns.get(fest_key, -40) >= 40
            and engine.weather.current.name == "sunny"
            and random.random() < 0.10):
        _community_cooldowns[fest_key] = tick
        primary = engine.sims[0].sim_id if engine.sims else ""
        c = build_consequences(EventType.FESTIVAL, primary, [], engine)
        events.append(LifeEvent.make(
            EventType.FESTIVAL, primary,
            "A neighbourhood festival breaks out — everyone is invited!",
            tick, visibility=Visibility.PUBLIC,
            valence=+0.8, intensity=0.8, duration_ticks=10,
            consequences=c, source="trigger:community",
        ))

    # Local celebration: random positive community event
    celeb_key = "local_celeb"
    if (tick - _community_cooldowns.get(celeb_key, -30) >= 35
            and random.random() < 0.06):
        _community_cooldowns[celeb_key] = tick
        primary = engine.sims[0].sim_id if engine.sims else ""
        c = build_consequences(EventType.LOCAL_CELEBRATION, primary, [], engine)
        events.append(LifeEvent.make(
            EventType.LOCAL_CELEBRATION, primary,
            "The community gathers to celebrate a local milestone.",
            tick, visibility=Visibility.PUBLIC,
            valence=+0.7, intensity=0.6, duration_ticks=8,
            consequences=c, source="trigger:community",
        ))

    # Neighbourhood dispute: two sims with enemy-level relationship + public
    dispute_key = "nd"
    if tick - _community_cooldowns.get(dispute_key, -25) >= 30:
        for sim in engine.sims:
            for other in engine.sims:
                if other.sim_id <= sim.sim_id:
                    continue
                rel = engine.relationships.get(sim.sim_id, other.sim_id)
                if rel.friendship <= -50 and random.random() < 0.08:
                    _community_cooldowns[dispute_key] = tick
                    c = build_consequences(EventType.NEIGHBORHOOD_DISPUTE,
                                           sim.sim_id, [other.sim_id], engine)
                    events.append(LifeEvent.make(
                        EventType.NEIGHBORHOOD_DISPUTE, sim.sim_id,
                        f"{sim.name} and {other.name} have a very public neighbourhood dispute.",
                        tick, secondary_sim_ids=[other.sim_id],
                        visibility=Visibility.PUBLIC,
                        valence=-0.5, intensity=0.65, duration_ticks=20,
                        consequences=c, source="trigger:community",
                    ))
                    break
            else:
                continue
            break

    return events


# ── Household triggers ─────────────────────────────────────────────────────────

_household_cooldowns: dict[str, int] = {}


def _check_household(engine, tick):
    from core.event_record import EventType, Visibility
    events = []

    for hh in engine.households:
        members = [engine._sim_lookup[mid] for mid in hh.member_ids if mid in engine._sim_lookup]
        if not members:
            continue

        hh_key = f"hh_{hh.id}"

        # Bills crisis: household funds critically low
        if (hh.funds < 50
                and tick - _household_cooldowns.get(hh_key + "_bills", -20) >= 20
                and random.random() < 0.25):
            _household_cooldowns[hh_key + "_bills"] = tick
            primary = members[0].sim_id
            c = build_consequences(EventType.BILLS_CRISIS, primary,
                                   [m.sim_id for m in members[1:]], engine)
            events.append(LifeEvent.make(
                EventType.BILLS_CRISIS, primary,
                f"The {hh.name} household can't pay the bills — financial crisis.",
                tick, secondary_sim_ids=[m.sim_id for m in members[1:]],
                visibility=Visibility.HOUSEHOLD,
                valence=-0.7, intensity=0.8, duration_ticks=15,
                consequences=c, source="trigger:household",
            ))

        # Eviction risk: funds negative for extended period
        if (hh.funds < 0
                and tick - _household_cooldowns.get(hh_key + "_evict", -30) >= 30):
            _household_cooldowns[hh_key + "_evict"] = tick
            primary = members[0].sim_id
            c = build_consequences(EventType.EVICTION_RISK, primary,
                                   [m.sim_id for m in members[1:]], engine)
            events.append(LifeEvent.make(
                EventType.EVICTION_RISK, primary,
                f"The {hh.name} household faces eviction — funds exhausted.",
                tick, secondary_sim_ids=[m.sim_id for m in members[1:]],
                visibility=Visibility.HOUSEHOLD,
                valence=-0.9, intensity=0.9, duration_ticks=20,
                consequences=c, source="trigger:household",
            ))

        # Roommate conflict: two housemates at very negative friendship
        if len(members) >= 2:
            for i, ma in enumerate(members):
                for mb in members[i+1:]:
                    rel = engine.relationships.get(ma.sim_id, mb.sim_id)
                    rk = f"rmc_{min(ma.sim_id,mb.sim_id)}_{max(ma.sim_id,mb.sim_id)}"
                    if (rel.friendship < -30
                            and tick - _household_cooldowns.get(rk, -25) >= 25
                            and random.random() < 0.12):
                        _household_cooldowns[rk] = tick
                        c = build_consequences(EventType.ROOMMATE_CONFLICT,
                                               ma.sim_id, [mb.sim_id], engine)
                        events.append(LifeEvent.make(
                            EventType.ROOMMATE_CONFLICT, ma.sim_id,
                            f"{ma.name} and {mb.name} have a serious roommate conflict.",
                            tick, secondary_sim_ids=[mb.sim_id],
                            visibility=Visibility.HOUSEHOLD,
                            valence=-0.6, intensity=0.7, duration_ticks=15,
                            consequences=c, source="trigger:household",
                        ))

    return events

