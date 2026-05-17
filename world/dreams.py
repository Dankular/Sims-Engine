"""
world/dreams.py — Dream mechanics for sleeping Sims.

Each sleep tick has a small chance of generating a dream. Dreams come in types
(motive, skill, romance, career, nightmare) and resolve as good/neutral/bad based
on the Sim's current emotional state and needs.  Effects apply immediately via
moodlets and small need/skill adjustments.
"""
from __future__ import annotations
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine

DREAM_CHANCE_PER_TICK = 0.12

DREAM_DEFS: list[dict] = [
    {
        "type": "energy",
        "label": "dream about waking up",
        "outcomes": {
            "bad":     {"moodlet": "exhausted",           "need": ("energy", -15)},
            "neutral": {"moodlet": None,                  "need": ("energy", +10)},
            "good":    {"moodlet": "well_rested",         "need": ("energy", +20)},
        },
    },
    {
        "type": "social",
        "label": "dream about social life",
        "outcomes": {
            "bad":     {"moodlet": "lonely",              "need": ("social", -10)},
            "neutral": {"moodlet": None,                  "need": ("social", +5)},
            "good":    {"moodlet": "social_butterfly",    "need": ("social", +15)},
        },
    },
    {
        "type": "food",
        "label": "dream about food",
        "outcomes": {
            "bad":     {"moodlet": "hungry_pangs",        "need": ("hunger", -15)},
            "neutral": {"moodlet": None,                  "need": ("hunger", +5)},
            "good":    {"moodlet": "delicious_meal",      "need": ("hunger", +15)},
        },
    },
    {
        "type": "creative_skill",
        "label": "dream about creativity",
        "outcomes": {
            "bad":     {"moodlet": "in_a_slump",          "skill": ("painting", -0.05)},
            "neutral": {"moodlet": None,                  "skill": ("painting", +0.10)},
            "good":    {"moodlet": "inspired",            "skill": ("painting", +0.30)},
        },
    },
    {
        "type": "fitness_skill",
        "label": "dream about working out",
        "outcomes": {
            "bad":     {"moodlet": "exhausted",           "skill": ("fitness", -0.05)},
            "neutral": {"moodlet": None,                  "skill": ("fitness", +0.10)},
            "good":    {"moodlet": "runners_high",        "skill": ("fitness", +0.30)},
        },
    },
    {
        "type": "logic_skill",
        "label": "dream about logic and puzzles",
        "outcomes": {
            "bad":     {"moodlet": "confused",            "skill": ("logic", 0.0)},
            "neutral": {"moodlet": None,                  "skill": ("logic", +0.10)},
            "good":    {"moodlet": "mathematically_minded","skill": ("logic", +0.30)},
        },
    },
    {
        "type": "charisma_skill",
        "label": "dream about public speaking",
        "outcomes": {
            "bad":     {"moodlet": "stressed",            "skill": ("charisma", 0.0)},
            "neutral": {"moodlet": None,                  "skill": ("charisma", +0.10)},
            "good":    {"moodlet": "feeling_confident",   "skill": ("charisma", +0.30)},
        },
    },
    {
        "type": "music_skill",
        "label": "dream about playing music",
        "outcomes": {
            "bad":     {"moodlet": "in_a_slump",          "skill": ("guitar", 0.0)},
            "neutral": {"moodlet": None,                  "skill": ("guitar", +0.10)},
            "good":    {"moodlet": "in_a_creative_flow",  "skill": ("guitar", +0.30)},
        },
    },
    {
        "type": "romance",
        "label": "dream about romance",
        "outcomes": {
            "bad":     {"moodlet": "heartbroken",         "need": ("social", -5)},
            "neutral": {"moodlet": "flirty",              "need": ("social", 0)},
            "good":    {"moodlet": "head_over_heels",     "need": ("social", +10)},
        },
    },
    {
        "type": "career",
        "label": "dream about career",
        "outcomes": {
            "bad":     {"moodlet": "stressed",            "need": (None, 0)},
            "neutral": {"moodlet": "hard_at_work",        "need": (None, 0)},
            "good":    {"moodlet": "just_promoted",       "need": (None, 0)},
        },
    },
    {
        "type": "nightmare",
        "label": "nightmare",
        "outcomes": {
            "bad":     {"moodlet": "terrified",           "need": ("energy", -10)},
            "neutral": {"moodlet": "spooked",             "need": (None, 0)},
            "good":    {"moodlet": "nightmare",           "need": (None, 0)},
        },
    },
    {
        "type": "home",
        "label": "dream about home and family",
        "outcomes": {
            "bad":     {"moodlet": "homesick",            "need": (None, 0)},
            "neutral": {"moodlet": None,                  "need": (None, 0)},
            "good":    {"moodlet": "home_sweet_home",     "need": ("social", +5)},
        },
    },
    {
        "type": "wealth",
        "label": "dream about riches",
        "outcomes": {
            "bad":     {"moodlet": "broke",               "need": (None, 0)},
            "neutral": {"moodlet": None,                  "need": (None, 0)},
            "good":    {"moodlet": "wealthy",             "need": (None, 0)},
        },
    },
    {
        "type": "writing_skill",
        "label": "dream about writing",
        "outcomes": {
            "bad":     {"moodlet": "in_a_slump",          "skill": ("writing", 0.0)},
            "neutral": {"moodlet": None,                  "skill": ("writing", +0.10)},
            "good":    {"moodlet": "muse_visited",        "skill": ("writing", +0.30)},
        },
    },
    {
        "type": "gardening_skill",
        "label": "dream about nature and the outdoors",
        "outcomes": {
            "bad":     {"moodlet": "uncomfortable",       "skill": ("gardening", 0.0)},
            "neutral": {"moodlet": "good_vibes",          "skill": ("gardening", +0.10)},
            "good":    {"moodlet": "sunshine_mood",       "skill": ("gardening", +0.25)},
        },
    },
]


def _roll_outcome(sim: "Sim") -> str:
    good_p = 0.40
    bad_p  = 0.25

    if hasattr(sim, "moodlets"):
        net = sim.moodlets.net_valence()
        good_p += net * 0.25
        bad_p  -= net * 0.20

    low_needs = sum(
        1 for attr in ("energy", "hunger", "social")
        if getattr(sim.needs, attr, 100) < 25
    )
    bad_p += low_needs * 0.10

    if getattr(sim, "grief_stage", -1) >= 1:
        bad_p += 0.20

    good_p = max(0.05, min(0.80, good_p))
    bad_p  = max(0.05, min(0.70, bad_p))
    neutral_p = max(0.05, 1.0 - good_p - bad_p)

    r = random.random()
    if r < bad_p:
        return "bad"
    if r < bad_p + neutral_p:
        return "neutral"
    return "good"


class DreamSystem:
    def try_dream(self, sim: "Sim", engine: "SimEngine") -> None:
        if random.random() > DREAM_CHANCE_PER_TICK:
            return

        dream = random.choice(DREAM_DEFS)
        outcome = _roll_outcome(sim)
        effects = dream["outcomes"][outcome]

        # Need delta
        need_name, need_delta = effects.get("need", (None, 0))
        if need_name and need_delta:
            cur = getattr(sim.needs, need_name, None)
            if cur is not None:
                setattr(sim.needs, need_name, max(0.0, min(100.0, cur + need_delta)))

        # Skill gain
        skill_entry = effects.get("skill")
        if skill_entry and hasattr(sim, "skills"):
            skill, amount = skill_entry
            if amount > 0:
                leveled = sim.skills.gain_xp(skill, amount)
                if leveled and hasattr(sim, "moodlets"):
                    sim.moodlets.add("skill_mastered" if sim.skills.level(skill) == 10 else "proud",
                                     source=f"dream_levelup:{skill}")

        # Moodlet
        moodlet_key = effects.get("moodlet")
        if moodlet_key and hasattr(sim, "moodlets"):
            sim.moodlets.add(moodlet_key, source=f"dream:{dream['type']}")

        sim._last_dream = {
            "type": dream["type"],
            "label": dream["label"],
            "outcome": outcome,
        }
