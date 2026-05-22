"""
core/lifetime_wish.py — One overarching life goal per sim.

Each aspiration maps to a lifetime wish with:
  - A description
  - A progress check function (called each tick)
  - A completion reward (emotion boost + trait unlock hint)

Progress tracking uses sim state directly — no additional counters needed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine


@dataclass
class LifetimeWish:
    aspiration: str
    description: str
    fulfilled: bool = False
    fulfilled_tick: int = -1
    _progress_cache: float = 0.0  # 0.0..1.0 for display


# ── Progress checkers (engine is passed for cross-sim queries) ────────────────

def _fortune_progress(sim: "Sim", engine: "SimEngine") -> float:
    target = 50_000.0
    return min(1.0, sim.simoleons / target)


def _family_progress(sim: "Sim", engine: "SimEngine") -> float:
    children = [s for s in engine.sims if sim.sim_id in s.parent_ids]
    # Need 3 children who have reached adulthood (age >= 18)
    adult_children = [c for c in children if c.profile.get("age", 0) >= 18]
    return min(1.0, len(adult_children) / 3.0)


def _popularity_progress(sim: "Sim", engine: "SimEngine") -> float:
    high_friends = sum(
        1 for other in engine.sims
        if other.sim_id != sim.sim_id
        and engine.relationships.get(sim.sim_id, other.sim_id).friendship >= 80
    )
    return min(1.0, high_friends / 10.0)


def _knowledge_progress(sim: "Sim", engine: "SimEngine") -> float:
    levels = sim.skills.levels
    if not levels:
        return 0.0
    # Mastery = all 6 skills at level 8+
    mastered = sum(1 for v in levels.values() if v >= 8)
    return min(1.0, mastered / max(1, len(levels)))


def _romance_progress(sim: "Sim", engine: "SimEngine") -> float:
    # 3+ sims ever reached romance >= 55 with this sim
    serious = sum(
        1 for other in engine.sims
        if other.sim_id != sim.sim_id
        and engine.relationships.get(sim.sim_id, other.sim_id).romance >= 55
    )
    return min(1.0, serious / 3.0)


def _creative_progress(sim: "Sim", engine: "SimEngine") -> float:
    # Proxy: creative_reputation >= 80
    return min(1.0, getattr(sim, "creative_reputation", 0.0) / 80.0)


_PROGRESS_FN: dict[str, Callable] = {
    "Fortune":    _fortune_progress,
    "Family":     _family_progress,
    "Popularity": _popularity_progress,
    "Knowledge":  _knowledge_progress,
    "Romance":    _romance_progress,
    "Creative":   _creative_progress,
}

_DESCRIPTIONS: dict[str, str] = {
    "Fortune":    "Accumulate §50,000 simoleons",
    "Family":     "Raise 3 children to adulthood",
    "Popularity": "Forge deep friendships with 10 sims (friendship ≥ 80)",
    "Knowledge":  "Master all skills to level 8",
    "Romance":    "Have 3 serious romantic partners (romance ≥ 55)",
    "Creative":   "Build a creative reputation of 80+",
}

_REWARDS: dict[str, dict] = {
    "Fortune":    {"emotion": "pride",      "intensity": 1.0, "duration": 20, "simoleon_bonus": 5000},
    "Family":     {"emotion": "joy",        "intensity": 1.0, "duration": 20},
    "Popularity": {"emotion": "admiration", "intensity": 1.0, "duration": 20},
    "Knowledge":  {"emotion": "optimism",   "intensity": 1.0, "duration": 20},
    "Romance":    {"emotion": "love",       "intensity": 1.0, "duration": 20},
    "Creative":   {"emotion": "pride",      "intensity": 1.0, "duration": 20, "creative_rep_bonus": 20},
}


def generate_wish(aspiration: str) -> LifetimeWish:
    return LifetimeWish(
        aspiration=aspiration,
        description=_DESCRIPTIONS.get(aspiration, f"Achieve your {aspiration} aspiration"),
    )


def check_wish(sim: "Sim", engine: "SimEngine", current_tick: int) -> bool:
    """
    Check progress on sim's lifetime wish. If fulfilled, apply reward and return True.
    Should be called once per N ticks from engine.run_tick.
    """
    wish: LifetimeWish | None = getattr(sim, "lifetime_wish", None)
    if wish is None or wish.fulfilled:
        return False

    fn = _PROGRESS_FN.get(wish.aspiration)
    if fn is None:
        return False

    try:
        progress = fn(sim, engine)
        wish._progress_cache = progress
    except Exception:
        return False

    if progress >= 1.0:
        wish.fulfilled = True
        wish.fulfilled_tick = current_tick
        _apply_wish_reward(sim, wish)
        return True

    return False


def _apply_wish_reward(sim: "Sim", wish: LifetimeWish) -> None:
    reward = _REWARDS.get(wish.aspiration, {})
    emo = reward.get("emotion", "joy")
    intensity = reward.get("intensity", 0.8)
    duration  = reward.get("duration", 15)
    sim.emotion.add(emo, intensity, duration=duration, source="lifetime_wish")

    bonus_cash = reward.get("simoleon_bonus", 0)
    if bonus_cash:
        _eng = getattr(sim, '_engine_ref', None)
    if _eng:
        from persistence.ledger import TX_LIFETIME_REWARD
        _eng._tx(sim, bonus_cash, TX_LIFETIME_REWARD, description='lifetime wish cash bonus')
    else:
        sim.simoleons += bonus_cash

    bonus_rep = reward.get("creative_rep_bonus", 0)
    if bonus_rep:
        sim.creative_reputation = min(100, sim.creative_reputation + bonus_rep)
