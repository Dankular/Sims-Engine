"""
core/goals.py — Goal/Intent persistence layer (System 4).

Sims form multi-tick goals that override random scheduler picks, giving
motivated behaviour instead of noise-driven interaction selection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.sim import Sim

# action_type → interaction string injected into scheduler
ACTION_TYPE_INTERACTION: dict[str, str] = {
    "seek_comfort":  "reach out to share feelings and seek comfort",
    "apologise":     "sincerely apologise for recent hurtful behaviour",
    "express_love":  "express deep affection and appreciation",
    "mentor":        "offer guidance and share hard-won knowledge",
    "reconcile":     "attempt to genuinely repair the relationship",
    "confront":      "calmly confront about painful or hurtful behaviour",
    "confide":       "open up and share a deep personal worry",
    "celebrate":     "share exciting news and celebrate together",
}

# Life-event type → (action_type, urgency 0-1, duration ticks)
_LIFE_EVENT_GOAL_MAP: dict[str, tuple[str, float, int]] = {
    "loss":          ("seek_comfort",  0.90, 15),
    "health_scare":  ("confide",       0.70, 10),
    "promotion":     ("celebrate",     0.60,  8),
    "burnout":       ("seek_comfort",  0.80, 12),
}

# Arc-state → (action_type, urgency, duration)
_ARC_GOAL_MAP: dict[str, tuple[str, float, int]] = {
    "grief:bargaining": ("apologise",     0.70,  8),
    "grief:depression": ("confide",       0.80, 10),
    "grief:acceptance": ("express_love",  0.50,  6),
    "loneliness":       ("seek_comfort",  0.60,  6),
}


@dataclass
class SimGoal:
    action_type: str    # key into ACTION_TYPE_INTERACTION
    target_sim: str     # sim_id of the intended target
    urgency: float      # 0-1; used as interaction weight bonus
    expiry_tick: int    # tick after which this goal is abandoned
    source: str         # e.g. "grief:bargaining", "life_event:loss"
    achieved: bool = False  # set True when fulfilled before expiry


def is_goal_valid(goal: SimGoal, current_tick: int) -> bool:
    return current_tick < goal.expiry_tick


def goal_to_interaction(goal: SimGoal) -> str:
    return ACTION_TYPE_INTERACTION.get(goal.action_type, f"[GOAL: {goal.action_type}]")


def set_goal_from_life_event(
    sim: "Sim",
    event_type: str,
    target_id: str,
    current_tick: int,
) -> None:
    """Assign an intent goal after a life event fires."""
    if event_type not in _LIFE_EVENT_GOAL_MAP:
        return
    action, urgency, duration = _LIFE_EVENT_GOAL_MAP[event_type]
    sim._active_goal = SimGoal(
        action_type=action,
        target_sim=target_id,
        urgency=urgency,
        expiry_tick=current_tick + duration,
        source=f"life_event:{event_type}",
    )


def set_goal_from_arc(
    sim: "Sim",
    arc_state: str,
    target_id: str,
    current_tick: int,
) -> None:
    """Assign an intent goal from a behavioural arc state."""
    if arc_state not in _ARC_GOAL_MAP:
        return
    action, urgency, duration = _ARC_GOAL_MAP[arc_state]
    # Don't overwrite a higher-urgency goal
    existing = getattr(sim, "_active_goal", None)
    if existing and is_goal_valid(existing, current_tick) and existing.urgency >= urgency:
        return
    sim._active_goal = SimGoal(
        action_type=action,
        target_sim=target_id,
        urgency=urgency,
        expiry_tick=current_tick + duration,
        source=arc_state,
    )


def clear_expired_goal(sim: "Sim", current_tick: int) -> None:
    """Remove goal if expired. Fires disappointment emotion on failure (Gap 4)."""
    goal = getattr(sim, "_active_goal", None)
    if goal is None or is_goal_valid(goal, current_tick):
        return
    if not goal.achieved:
        # Goal failed — emotional and personality consequence
        sim.emotion.add("disappointment", 0.6, duration=4, source=f"goal_failed:{goal.action_type}")
        if goal.urgency >= 0.7:
            # High-stakes failure — mild neuroticism drift + trauma log
            sim.profile["ocean"]["neuroticism"] = min(
                1.0, sim.profile["ocean"]["neuroticism"] + 0.01
            )
            if not hasattr(sim, "trauma_events"):
                sim.trauma_events = []
            sim.trauma_events.append(f"failed_goal:{goal.action_type}:{goal.source}")
    sim._active_goal = None


def mark_goal_achieved(sim: "Sim") -> None:
    """Call when a goal interaction is successfully resolved."""
    goal = getattr(sim, "_active_goal", None)
    if goal is not None:
        goal.achieved = True
