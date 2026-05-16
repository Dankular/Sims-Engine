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


# NLI goal-inference labels → action_type mapping
_NLI_GOAL_LABELS: dict[str, str] = {
    "seek emotional comfort from a friend":    "seek_comfort",
    "seek financial help or advice":           "seek_comfort",
    "recover from career setback":             "celebrate",
    "celebrate good news with someone":        "celebrate",
    "confide a personal secret or worry":      "confide",
    "apologise for past behaviour":            "apologise",
    "withdraw from social contact":            "confide",
    "express love or gratitude":               "express_love",
}
_NLI_GOAL_LABEL_LIST = list(_NLI_GOAL_LABELS.keys())


def _infer_action_from_narrative(event_type: str, narrative: str) -> str | None:
    """System 4 — Use NLI to infer goal type from life-event narrative text."""
    try:
        from llm.small_models import zero_shot_classify, get_goal_nli
        clf = get_goal_nli()
        if clf is None:
            return None
        text = f"{event_type}: {narrative}"
        result = zero_shot_classify(text, _NLI_GOAL_LABEL_LIST, pipeline=clf, threshold=0.35)
        if result:
            return _NLI_GOAL_LABELS.get(result[0])
    except Exception:
        pass
    return None


def set_goal_from_life_event(
    sim: "Sim",
    event_type: str,
    target_id: str,
    current_tick: int,
    narrative: str = "",
) -> None:
    """Assign an intent goal after a life event fires."""
    # System 4: try NLI inference first; fall back to hardcoded map
    action = None
    if narrative:
        action = _infer_action_from_narrative(event_type, narrative)

    if action is None:
        if event_type not in _LIFE_EVENT_GOAL_MAP:
            return
        action, urgency, duration = _LIFE_EVENT_GOAL_MAP[event_type]
    else:
        _, urgency, duration = _LIFE_EVENT_GOAL_MAP.get(
            event_type, ("seek_comfort", 0.7, 10)
        )

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
