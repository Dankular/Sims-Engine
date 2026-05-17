"""
core/aspiration_rewards.py — Milestone-based perk trees per aspiration.

Each aspiration has 3 milestones. Completing a milestone unlocks a tangible
reward: need bonus, interaction unlock, skill XP multiplier, or trait.

Milestone progress is checked in engine.run_tick every 10 ticks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine


@dataclass
class Milestone:
    label: str
    description: str
    threshold: float    # 0..1 progress fraction to unlock
    reward_type: str    # "need_boost", "interaction_unlock", "skill_bonus", "trait"
    reward_value: str   # string payload (need name, interaction, skill, trait label)
    reward_amount: float = 1.0
    completed: bool = False


@dataclass
class AspirationProgress:
    aspiration: str
    milestones: list[Milestone] = field(default_factory=list)
    total_xp: float = 0.0       # accumulates from related interactions
    active_bonuses: list[str] = field(default_factory=list)  # currently active rewards


# ── Milestone definitions per aspiration ──────────────────────────────────────

def _fortune_milestones() -> list[Milestone]:
    return [
        Milestone("Saver",      "Accumulate §5,000",            0.10, "need_boost",          "fun",      5.0),
        Milestone("Investor",   "Accumulate §20,000",           0.40, "interaction_unlock",  "negotiate deal", 1.0),
        Milestone("Mogul",      "Accumulate §40,000",           0.80, "skill_bonus",         "charisma", 2.0),
    ]

def _family_milestones() -> list[Milestone]:
    return [
        Milestone("Caregiver",  "Have your first child",        0.20, "need_boost",          "social",   8.0),
        Milestone("Nurturer",   "Raise 2 children",             0.60, "interaction_unlock",  "give life advice", 1.0),
        Milestone("Patriarch",  "Raise 3 children to adulthood",1.00, "trait",               "family-oriented", 1.0),
    ]

def _popularity_milestones() -> list[Milestone]:
    return [
        Milestone("Socialite",  "Have 3 close friends",         0.25, "need_boost",          "social",   10.0),
        Milestone("Networker",  "Have 6 friends (≥50)",         0.55, "interaction_unlock",  "host gathering", 1.0),
        Milestone("Icon",       "Have 10 friends (≥80)",        1.00, "skill_bonus",         "charisma", 3.0),
    ]

def _knowledge_milestones() -> list[Milestone]:
    return [
        Milestone("Student",    "Max out one skill",            0.25, "need_boost",          "fun",      6.0),
        Milestone("Scholar",    "Max out three skills",         0.60, "skill_bonus",         "logic",    2.0),
        Milestone("Sage",       "Master all skills (level 8)",  1.00, "interaction_unlock",  "teach mastery class", 1.0),
    ]

def _romance_milestones() -> list[Milestone]:
    return [
        Milestone("Charmer",    "First serious partner (romance≥55)",  0.25, "need_boost",   "fun",      5.0),
        Milestone("Lover",      "Two serious partners over lifetime",   0.60, "interaction_unlock", "serenade", 1.0),
        Milestone("Casanova",   "Three serious partners",               1.00, "skill_bonus",  "charisma", 2.0),
    ]

def _creative_milestones() -> list[Milestone]:
    return [
        Milestone("Hobbyist",   "Creative reputation reaches 20",  0.25, "need_boost",       "fun",      8.0),
        Milestone("Artist",     "Creative reputation reaches 50",  0.60, "interaction_unlock","perform original piece", 1.0),
        Milestone("Virtuoso",   "Creative reputation reaches 80",  1.00, "skill_bonus",      "creativity", 3.0),
    ]

_MILESTONE_FACTORIES = {
    "Fortune":    _fortune_milestones,
    "Family":     _family_milestones,
    "Popularity": _popularity_milestones,
    "Knowledge":  _knowledge_milestones,
    "Romance":    _romance_milestones,
    "Creative":   _creative_milestones,
}


def generate_progress(aspiration: str) -> AspirationProgress:
    factory = _MILESTONE_FACTORIES.get(aspiration)
    milestones = factory() if factory else []
    return AspirationProgress(aspiration=aspiration, milestones=milestones)


def tick_aspiration(sim: "Sim", engine: "SimEngine", current_tick: int) -> list[str]:
    """
    Check milestone completion for a sim. Apply rewards for newly completed milestones.
    Returns list of newly completed milestone labels.
    """
    prog: AspirationProgress | None = getattr(sim, "aspiration_progress", None)
    if prog is None:
        return []

    wish = getattr(sim, "lifetime_wish", None)
    overall = wish._progress_cache if wish else 0.0

    newly_completed = []
    for ms in prog.milestones:
        if ms.completed:
            continue
        if overall >= ms.threshold or _milestone_direct_check(ms, sim, engine):
            ms.completed = True
            _apply_milestone_reward(ms, sim)
            prog.active_bonuses.append(ms.reward_value)
            newly_completed.append(ms.label)

    return newly_completed


def _milestone_direct_check(ms: Milestone, sim: "Sim", engine: "SimEngine") -> bool:
    """Per-milestone direct checks independent of overall wish progress."""
    asp = getattr(sim, "aspiration_progress", None)
    if asp is None:
        return False
    aspiration = asp.aspiration

    try:
        if aspiration == "Fortune":
            thresholds = [5_000, 20_000, 40_000]
            idx = asp.milestones.index(ms)
            return sim.simoleons >= thresholds[idx]

        if aspiration == "Popularity":
            friends = sum(
                1 for o in engine.sims
                if o.sim_id != sim.sim_id
                and engine.relationships.get(sim.sim_id, o.sim_id).friendship >= 50
            )
            thresholds = [3, 6, 10]
            idx = asp.milestones.index(ms)
            return friends >= thresholds[idx]

        if aspiration == "Creative":
            thresholds = [20, 50, 80]
            idx = asp.milestones.index(ms)
            return sim.creative_reputation >= thresholds[idx]
    except (ValueError, IndexError):
        pass
    return False


def _apply_milestone_reward(ms: Milestone, sim: "Sim") -> None:
    if ms.reward_type == "need_boost":
        need = ms.reward_value
        current = getattr(sim.needs, need, 0)
        setattr(sim.needs, need, min(100.0, current + ms.reward_amount))
        sim.emotion.add("joy", 0.6, duration=8, source=f"milestone:{ms.label}")

    elif ms.reward_type == "skill_bonus":
        skill = ms.reward_value
        sim.skills.gain_xp(skill, ms.reward_amount)
        sim.emotion.add("pride", 0.7, duration=8, source=f"milestone:{ms.label}")

    elif ms.reward_type == "interaction_unlock":
        if not hasattr(sim, "_unlocked_interactions"):
            sim._unlocked_interactions = []
        sim._unlocked_interactions.append(ms.reward_value)
        sim.emotion.add("optimism", 0.6, duration=6, source=f"milestone:{ms.label}")

    elif ms.reward_type == "trait":
        traits = sim.profile.get("traits", [])
        if ms.reward_value not in traits:
            sim.profile["traits"] = traits + [ms.reward_value]
        sim.emotion.add("pride", 0.8, duration=10, source=f"milestone:{ms.label}")
