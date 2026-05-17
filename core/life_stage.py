"""
core/life_stage.py — Sim aging, life stage transitions, and death.

Life stages (by age):
  child       0-12   — inherited traits, learning focus
  teen        13-17  — social experimentation, identity
  young_adult 18-25  — peak energy, ambition, romance
  adult       26-59  — career/family, stable relationships
  elder       60+    — wisdom, slower energy, health vulnerability

Death age is assigned lazily per-sim (random 72–85) and stored on the
profile so it survives serialisation.  When a sim dies, the engine emits
a "sim_died" event and removes them from the active roster.
"""
from __future__ import annotations

import random
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
DEATH_AGE_MIN = 72
DEATH_AGE_MAX = 85

LIFE_STAGES: list[tuple[str, int, int]] = [
    ("child",       0,  12),
    ("teen",       13,  17),
    ("young_adult",18,  25),
    ("adult",      26,  59),
    ("elder",      60, 999),
]

# Elder age → extra energy drain per tick
_ELDER_DRAIN_BASE = 0.20   # at age 60
_ELDER_DRAIN_SLOPE = 0.015  # additional per year past 60

# OCEAN nudges on stage transition (applied once)
_STAGE_OCEAN_NUDGE: dict[str, dict[str, float]] = {
    "teen":        {"openness": +0.04, "neuroticism": +0.03},
    "young_adult": {"extraversion": +0.03, "agreeableness": -0.02},
    "adult":       {"conscientiousness": +0.03, "neuroticism": -0.02},
    "elder":       {"openness": -0.03, "agreeableness": +0.04, "neuroticism": +0.02},
}


def get_life_stage(age: int) -> str:
    for name, lo, hi in LIFE_STAGES:
        if lo <= age <= hi:
            return name
    return "elder"


def advance_age(sim: "Sim") -> tuple[int, str, str]:
    """
    Increment the sim's age by one year.
    Returns (new_age, old_stage, new_stage).
    """
    old_age   = sim.profile.get("age", 25)
    old_stage = get_life_stage(old_age)
    new_age   = old_age + 1
    sim.profile["age"] = new_age
    new_stage = get_life_stage(new_age)
    return new_age, old_stage, new_stage


def apply_stage_transition(sim: "Sim", old_stage: str, new_stage: str) -> list[str]:
    """
    Apply OCEAN nudges and emotions for a life-stage transition.
    Returns a list of log messages.
    """
    messages: list[str] = []
    if old_stage == new_stage:
        return messages

    messages.append(f"{sim.name} entered {new_stage.replace('_', ' ')} stage")

    # OCEAN nudge
    nudges = _STAGE_OCEAN_NUDGE.get(new_stage, {})
    for trait, delta in nudges.items():
        current = sim.profile["ocean"].get(trait, 0.5)
        sim.profile["ocean"][trait] = round(max(0.0, min(1.0, current + delta)), 4)

    # Emotion
    emotion_map = {
        "teen":        ("nervousness", 0.4, 4),
        "young_adult": ("excitement",  0.6, 6),
        "adult":       ("pride",       0.5, 5),
        "elder":       ("nostalgia",   0.5, 6),
    }
    if new_stage in emotion_map:
        emo, intensity, dur = emotion_map[new_stage]
        sim.emotion.add(emo, intensity, duration=dur, source=f"stage:{new_stage}")

    return messages


def elder_tick_effects(sim: "Sim") -> None:
    """
    Apply additional energy drain and health vulnerability for elders.
    Called every tick for elder sims.
    """
    age = sim.profile.get("age", 0)
    if age < 60:
        return
    drain = _ELDER_DRAIN_BASE + _ELDER_DRAIN_SLOPE * (age - 60)
    sim.needs.energy = max(0, sim.needs.energy - drain)

    # Elders have reduced social need drain (more content alone)
    sim.needs.social = min(100, sim.needs.social + 0.1)


def assign_death_age(sim: "Sim") -> int:
    """Lazily assign and store the sim's death age. Returns it."""
    if "_death_age" not in sim.profile:
        base = random.randint(DEATH_AGE_MIN, DEATH_AGE_MAX)
        # Conscientiousness → longer life
        con = sim.profile["ocean"].get("conscientiousness", 0.5)
        # Neuroticism → shorter life
        neu = sim.profile["ocean"].get("neuroticism", 0.5)
        modifier = round((con - 0.5) * 4 - (neu - 0.5) * 3)
        sim.profile["_death_age"] = max(65, base + modifier)
    return sim.profile["_death_age"]


def should_die(sim: "Sim") -> bool:
    """Return True if the sim has reached their natural death age."""
    age      = sim.profile.get("age", 0)
    death_at = assign_death_age(sim)
    return age >= death_at


def years_remaining(sim: "Sim") -> int:
    return max(0, assign_death_age(sim) - sim.profile.get("age", 0))
