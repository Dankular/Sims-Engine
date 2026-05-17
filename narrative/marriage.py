"""
narrative/marriage.py — Marriage, wedding, and divorce mechanics.

Marriage:
  - Triggered when propose_marriage interaction resolves with valence > 0.6
  - Sets _married_to on both sims
  - Merges households if different
  - Schedules a wedding social event

Divorce:
  - Triggered when married couple's romance < DIVORCE_ROMANCE_THRESHOLD
    for DIVORCE_ROMANCE_TICKS_MIN consecutive ticks
  - Splits household (lower-agreeableness sim moves to new household)
  - Applies heartbreak sentiment + grief arc start
  - Children stay with the higher-agreeableness parent's household
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)

DIVORCE_ROMANCE_THRESHOLD = 15     # romance below this → danger zone
DIVORCE_ROMANCE_TICKS_MIN = 3      # ticks below threshold before divorce fires


# ── Marriage ──────────────────────────────────────────────────────────────────

def marry(sim_a: "Sim", sim_b: "Sim", engine: "SimEngine") -> None:
    """Formalise a marriage between two sims."""
    if getattr(sim_a, "_married_to", None) or getattr(sim_b, "_married_to", None):
        return  # already married

    sim_a._married_to = sim_b.sim_id
    sim_b._married_to = sim_a.sim_id
    sim_a._divorce_risk_ticks = 0
    sim_b._divorce_risk_ticks = 0

    # Emotional response
    for sim in (sim_a, sim_b):
        sim.emotion.add("love",      1.0, duration=30, source="marriage")
        sim.emotion.add("optimism",  0.8, duration=20, source="marriage")

    # Add first_love sentiment (if not already present)
    rel = engine.relationships.get(sim_a.sim_id, sim_b.sim_id)
    from core.sentiments import add_sentiment
    add_sentiment(rel, "first_love", engine.tick_count, source="marriage")

    # Merge households if different
    _merge_households(sim_a, sim_b, engine)

    # Schedule wedding social event
    _schedule_wedding(sim_a, sim_b, engine)

    logger.info("[Marriage] %s married %s at tick %d",
                sim_a.name, sim_b.name, engine.tick_count)

    engine._bus.emit(
        "married",
        sim_a=sim_a,
        sim_b=sim_b,
        tick=engine.tick_count,
    )


def _merge_households(sim_a: "Sim", sim_b: "Sim", engine: "SimEngine") -> None:
    if sim_a.household_id == sim_b.household_id:
        return
    # Move sim_b into sim_a's household (or create a new one)
    target_hh_id = sim_a.household_id
    if target_hh_id is None:
        # Neither has a household — create one
        from world.households import Household
        new_hh = Household(
            id=uuid.uuid4().hex[:8],
            name=f"{sim_a.name} & {sim_b.name} household",
            member_ids=[sim_a.sim_id, sim_b.sim_id],
        )
        engine.households.append(new_hh)
        sim_a.household_id = new_hh.id
        sim_b.household_id = new_hh.id
        return

    # Move sim_b (and any children) to sim_a's household
    old_hh_id = sim_b.household_id
    for hh in engine.households:
        if hh.id == target_hh_id and sim_b.sim_id not in hh.member_ids:
            hh.member_ids.append(sim_b.sim_id)
        if hh.id == old_hh_id and sim_b.sim_id in hh.member_ids:
            hh.member_ids.remove(sim_b.sim_id)
    sim_b.household_id = target_hh_id

    # Move children of sim_b along with them
    for sim in engine.sims:
        if sim_b.sim_id in sim.parent_ids and sim.household_id == old_hh_id:
            sim.household_id = target_hh_id
            for hh in engine.households:
                if hh.id == target_hh_id and sim.sim_id not in hh.member_ids:
                    hh.member_ids.append(sim.sim_id)
                if hh.id == old_hh_id and sim.sim_id in hh.member_ids:
                    hh.member_ids.remove(sim.sim_id)


def _schedule_wedding(sim_a: "Sim", sim_b: "Sim", engine: "SimEngine") -> None:
    if not hasattr(engine, "social_events"):
        return
    # Guest list: closest friends of both
    friends = sorted(
        [o for o in engine.sims if o.sim_id not in (sim_a.sim_id, sim_b.sim_id)],
        key=lambda o: (
            engine.relationships.get(sim_a.sim_id, o.sim_id).friendship
            + engine.relationships.get(sim_b.sim_id, o.sim_id).friendship
        ),
        reverse=True,
    )[:8]
    engine.social_events.schedule_wedding(sim_a, sim_b, engine.tick_count,
                                           [f.sim_id for f in friends])


# ── Divorce ───────────────────────────────────────────────────────────────────

def check_divorces(engine: "SimEngine") -> None:
    """Called each tick — detect and execute divorces."""
    processed: set[str] = set()

    for sim in engine.sims:
        spouse_id = getattr(sim, "_married_to", None)
        if not spouse_id or sim.sim_id in processed:
            continue

        spouse = engine._sim_lookup.get(spouse_id)
        if spouse is None:
            # Spouse died — clear the field
            sim._married_to = None
            continue

        rel = engine.relationships.get(sim.sim_id, spouse_id)

        if rel.romance < DIVORCE_ROMANCE_THRESHOLD:
            sim._divorce_risk_ticks    = getattr(sim,    "_divorce_risk_ticks", 0) + 1
            spouse._divorce_risk_ticks = getattr(spouse, "_divorce_risk_ticks", 0) + 1
        else:
            sim._divorce_risk_ticks    = 0
            spouse._divorce_risk_ticks = 0

        if (
            sim._divorce_risk_ticks >= DIVORCE_ROMANCE_TICKS_MIN
            and spouse._divorce_risk_ticks >= DIVORCE_ROMANCE_TICKS_MIN
        ):
            divorce(sim, spouse, engine)
            processed.update({sim.sim_id, spouse_id})


def divorce(sim_a: "Sim", sim_b: "Sim", engine: "SimEngine") -> None:
    """Execute a divorce between two married sims."""
    sim_a._married_to = None
    sim_b._married_to = None
    sim_a._divorce_risk_ticks = 0
    sim_b._divorce_risk_ticks = 0

    rel = engine.relationships.get(sim_a.sim_id, sim_b.sim_id)
    rel.romance = max(-20, rel.romance - 30)

    # Sentiments
    from core.sentiments import add_sentiment
    add_sentiment(rel, "heartbreak", engine.tick_count, source="divorce")

    # Grief arc for both
    for sim in (sim_a, sim_b):
        sim.grief_stage  = 0
        sim.grief_target = (sim_b if sim is sim_a else sim_a).sim_id
        sim._grief_tick_count = 0
        sim.emotion.add("grief", 1.0, duration=10, source="divorce")
        sim.emotion.add("disappointment", 0.8, duration=8, source="divorce")

    # Split household
    _split_household(sim_a, sim_b, engine)

    logger.info("[Divorce] %s and %s divorced at tick %d",
                sim_a.name, sim_b.name, engine.tick_count)

    engine._bus.emit(
        "divorced",
        sim_a=sim_a,
        sim_b=sim_b,
        tick=engine.tick_count,
    )


def _split_household(sim_a: "Sim", sim_b: "Sim", engine: "SimEngine") -> None:
    if sim_a.household_id != sim_b.household_id:
        return  # already separate

    # Higher agreeableness sim keeps the household; other moves out
    mover = sim_b if sim_a.ocean["agreeableness"] >= sim_b.ocean["agreeableness"] else sim_a
    stayer = sim_a if mover is sim_b else sim_b

    old_hh_id = mover.household_id
    from world.households import Household
    new_hh = Household(
        id=uuid.uuid4().hex[:8],
        name=f"{mover.name} household",
        member_ids=[mover.sim_id],
    )
    engine.households.append(new_hh)
    mover.household_id = new_hh.id

    for hh in engine.households:
        if hh.id == old_hh_id and mover.sim_id in hh.member_ids:
            hh.member_ids.remove(mover.sim_id)

    # Children stay with stayer
    for sim in engine.sims:
        if (sim_a.sim_id in sim.parent_ids or sim_b.sim_id in sim.parent_ids):
            if sim.household_id == old_hh_id:
                sim.household_id = stayer.household_id
