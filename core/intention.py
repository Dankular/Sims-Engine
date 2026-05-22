"""
core/intention.py — Persistent multi-tick intention system.

Sims maintain an IntentionStack of Goals that survive tick boundaries.
Each Goal decomposes into ordered SubGoals. Commitment decays when progress
stalls; a replanning trigger fires when commitment falls below threshold or
when the context changes enough to invalidate the current plan.

Engine integration:
  engine.run_tick() → engine._process_intentions()
  scheduler.choose_interaction() → reads sim.intentions.active_bias()
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from core.sim import Sim

logger = logging.getLogger(__name__)

# ── Goal taxonomy ─────────────────────────────────────────────────────────────

class GoalType(str, Enum):
    SAVE_MONEY         = "save_money"
    REPAIR_RELATIONSHIP= "repair_relationship"
    BUILD_FRIENDSHIP   = "build_friendship"
    FIND_ROMANCE       = "find_romance"
    START_BUSINESS     = "start_business"
    CAREER_ADVANCE     = "career_advance"
    LEARN_SKILL        = "learn_skill"
    LEAVE_HOUSEHOLD    = "leave_household"
    RESOLVE_CONFLICT   = "resolve_conflict"
    BUY_PROPERTY       = "buy_property"
    HAVE_CHILD         = "have_child"
    MAKE_AMENDS        = "make_amends"
    # Extended ambition goals
    BECOME_FAMOUS      = "become_famous"
    BUILD_EMPIRE       = "build_empire"
    DESTROY_RIVAL      = "destroy_rival"
    FORM_FACTION       = "form_faction"
    ACHIEVE_MASTERY    = "achieve_mastery"
    GAIN_POLITICAL_POWER = "gain_political_power"
    LEAVE_LEGACY       = "leave_legacy"
    FIND_PURPOSE       = "find_purpose"


class GoalStatus(str, Enum):
    PENDING   = "pending"
    ACTIVE    = "active"
    PAUSED    = "paused"
    FULFILLED = "fulfilled"
    ABANDONED = "abandoned"
    REPLANNING= "replanning"


@dataclass
class SubGoal:
    label: str                        # e.g. "apologise to Alex"
    interaction_bias: str             # interaction type to favour: "repair", "friendly" …
    target_sim_id: str = ""
    condition: Callable[["Sim"], bool] | None = None  # fulfilled when True
    completed: bool = False


@dataclass
class Goal:
    goal_type:      GoalType
    target_sim_id:  str = ""          # relevant other sim (empty for solo goals)
    target_value:   float = 0.0       # numeric milestone (simoleons threshold, skill level …)
    subgoals:       list[SubGoal] = field(default_factory=list)
    priority:       float = 0.5       # 0..1, higher = front of stack
    commitment:     float = 1.0       # decays 0..1; <REPLAN_THRESHOLD → replan
    deadline_ticks: int = 100         # 0 = no deadline
    ticks_active:   int = 0
    status:         GoalStatus = GoalStatus.PENDING
    created_at:     float = field(default_factory=time.time)

    # decay per tick when no progress
    DECAY_BASE    = 0.004
    DECAY_BLOCKED = 0.010
    REPLAN_THRESHOLD = 0.15

    def active_subgoal(self) -> SubGoal | None:
        for sg in self.subgoals:
            if not sg.completed:
                return sg
        return None

    def progress_fraction(self) -> float:
        if not self.subgoals:
            return 0.0
        done = sum(1 for sg in self.subgoals if sg.completed)
        return done / len(self.subgoals)

    def tick(self, sim: "Sim", had_relevant_interaction: bool) -> None:
        if self.status not in (GoalStatus.ACTIVE, GoalStatus.REPLANNING):
            return
        self.ticks_active += 1

        # Advance completed subgoals
        for sg in self.subgoals:
            if not sg.completed and sg.condition and sg.condition(sim):
                sg.completed = True
                self.commitment = min(1.0, self.commitment + 0.15)

        # Decay commitment
        decay = self.DECAY_BASE if had_relevant_interaction else self.DECAY_BLOCKED
        self.commitment = max(0.0, self.commitment - decay)

        # Deadline expiry
        if self.deadline_ticks > 0 and self.ticks_active >= self.deadline_ticks:
            self.status = GoalStatus.ABANDONED
            return

        # Replan trigger
        if self.commitment < self.REPLAN_THRESHOLD:
            self.status = GoalStatus.REPLANNING

        # Fulfillment check
        if all(sg.completed for sg in self.subgoals) and self.subgoals:
            self.status = GoalStatus.FULFILLED

    def interaction_bias(self) -> str | None:
        """Return the interaction type that should be preferred right now."""
        sg = self.active_subgoal()
        return sg.interaction_bias if sg else None

    def target_id(self) -> str | None:
        sg = self.active_subgoal()
        if sg and sg.target_sim_id:
            return sg.target_sim_id
        return self.target_sim_id or None


# ── Intention Stack ───────────────────────────────────────────────────────────

class IntentionStack:
    """
    Per-sim stack of Goals ordered by priority.  The top active goal
    drives interaction selection and determines subgoal targets.
    """

    MAX_GOALS = 5

    def __init__(self) -> None:
        self._goals: list[Goal] = []

    # ── API ───────────────────────────────────────────────────────────────────

    def push(self, goal: Goal) -> None:
        """Add a goal, evict lowest-priority if full."""
        self._goals.append(goal)
        self._goals.sort(key=lambda g: -g.priority)
        if len(self._goals) > self.MAX_GOALS:
            self._goals.pop()  # drop lowest priority

    def active_goal(self) -> Goal | None:
        for g in self._goals:
            if g.status == GoalStatus.ACTIVE:
                return g
        return None

    def active_bias(self) -> tuple[str | None, str | None]:
        """Returns (interaction_type_bias, target_sim_id) for the top active goal."""
        g = self.active_goal()
        if g is None:
            return None, None
        return g.interaction_bias(), g.target_id()

    def tick(self, sim: "Sim", recent_interaction_type: str = "") -> None:
        """Process all goals, activate pending, replan stale, prune abandoned."""
        # Activate the highest-priority pending goal if none active
        if not self.active_goal():
            for g in self._goals:
                if g.status == GoalStatus.PENDING:
                    g.status = GoalStatus.ACTIVE
                    break

        for g in self._goals:
            bias, _ = g.interaction_bias(), None
            relevant = (
                bias is not None
                and recent_interaction_type.startswith(bias[:4])
            )
            g.tick(sim, had_relevant_interaction=relevant)

        # Replan: reset commitment and shuffle subgoals
        for g in self._goals:
            if g.status == GoalStatus.REPLANNING:
                g.commitment = 0.5
                g.status = GoalStatus.ACTIVE
                random.shuffle(g.subgoals)  # try a different subgoal order

        # Prune
        self._goals = [g for g in self._goals
                       if g.status not in (GoalStatus.FULFILLED, GoalStatus.ABANDONED)]

    def summary(self) -> list[dict]:
        return [
            {
                "type":       g.goal_type,
                "status":     g.status,
                "priority":   round(g.priority, 2),
                "commitment": round(g.commitment, 2),
                "progress":   round(g.progress_fraction(), 2),
                "target":     g.target_sim_id,
            }
            for g in self._goals
        ]


# ── Goal factory ─────────────────────────────────────────────────────────────

def make_repair_goal(target_sim_id: str, priority: float = 0.8) -> Goal:
    return Goal(
        goal_type=GoalType.REPAIR_RELATIONSHIP,
        target_sim_id=target_sim_id,
        priority=priority,
        deadline_ticks=120,
        subgoals=[
            SubGoal("apologise", "repair", target_sim_id,
                    lambda s: s.needs.social > 40),
            SubGoal("reconcile", "friendly", target_sim_id,
                    lambda s: s.needs.fun > 50),
        ],
    )


def make_save_goal(target_simoleons: float, priority: float = 0.6) -> Goal:
    return Goal(
        goal_type=GoalType.SAVE_MONEY,
        target_value=target_simoleons,
        priority=priority,
        deadline_ticks=200,
        subgoals=[
            SubGoal("earn more", "activity", "",
                    lambda s, t=target_simoleons: s.simoleons >= t * 0.5),
            SubGoal("reach target", "activity", "",
                    lambda s, t=target_simoleons: s.simoleons >= t),
        ],
    )


def make_skill_goal(skill: str, target_level: int, priority: float = 0.5) -> Goal:
    return Goal(
        goal_type=GoalType.LEARN_SKILL,
        target_value=float(target_level),
        priority=priority,
        deadline_ticks=150,
        subgoals=[
            SubGoal(f"practice {skill}", "activity", "",
                    lambda s, sk=skill, lv=target_level:
                        s.skills.levels.get(sk, 0) >= lv),
        ],
    )


def make_fame_goal(priority: float = 0.7) -> Goal:
    return Goal(
        goal_type=GoalType.BECOME_FAMOUS,
        priority=priority,
        deadline_ticks=200,
        subgoals=[
            SubGoal("build reputation", "social", "",
                    lambda s: s.reputation_score >= 40),
            SubGoal("become celebrity", "social", "",
                    lambda s: s.reputation_score >= 70),
        ],
    )


def make_empire_goal(target_properties: int = 3, priority: float = 0.65) -> Goal:
    return Goal(
        goal_type=GoalType.BUILD_EMPIRE,
        target_value=float(target_properties),
        priority=priority,
        deadline_ticks=300,
        subgoals=[
            SubGoal("earn capital", "activity", "",
                    lambda s: s.simoleons >= 1000),
            SubGoal("acquire assets", "activity", "",
                    lambda s: s.simoleons >= 3000),
        ],
    )


def make_rival_goal(target_id: str, priority: float = 0.85) -> Goal:
    return Goal(
        goal_type=GoalType.DESTROY_RIVAL,
        target_sim_id=target_id,
        priority=priority,
        deadline_ticks=150,
        subgoals=[
            SubGoal("spread rumors", "conflict", target_id,
                    lambda s: True),  # completed by conflict interaction
            SubGoal("undermine rival", "conflict", target_id,
                    lambda s: True),
        ],
    )


def make_mastery_goal(skill: str, target_level: int = 8, priority: float = 0.6) -> Goal:
    mid_level = max(1, target_level // 2)
    return Goal(
        goal_type=GoalType.ACHIEVE_MASTERY,
        target_value=float(target_level),
        priority=priority,
        deadline_ticks=250,
        subgoals=[
            SubGoal(f"study {skill}", "activity", "",
                    lambda s, sk=skill, lv=mid_level:
                        s.skills.levels.get(sk, 0) >= lv),
            SubGoal(f"master {skill}", "activity", "",
                    lambda s, sk=skill, lv=target_level:
                        s.skills.levels.get(sk, 0) >= lv),
        ],
    )


def make_legacy_goal(priority: float = 0.5) -> Goal:
    return Goal(
        goal_type=GoalType.LEAVE_LEGACY,
        priority=priority,
        deadline_ticks=150,
        subgoals=[
            SubGoal("mentor successor", "mentor", "",
                    lambda s: getattr(s, "mentorship_count", 0) >= 3),
            SubGoal("leave inheritance", "activity", "",
                    lambda s: s.simoleons >= 2000),
        ],
    )


def maybe_generate_intention(sim: "Sim", tick: int) -> Goal | None:
    """
    Heuristically generate a new intention for a sim based on current state.
    Called by the engine every N ticks when the sim has no active goal.

    Priority order:
      1. Hard need-based triggers (social isolation, bankruptcy, grief)
      2. OCEAN personality-driven ambitions
      3. Life-stage milestones
      4. Relationship-state triggers
      5. Career ambition fallback
    """
    # ── 1. Urgent need-based triggers ─────────────────────────────────────────
    if sim.needs.social < 20:
        # Lonely → seek connection
        return Goal(
            goal_type=GoalType.BUILD_FRIENDSHIP,
            priority=0.75,
            deadline_ticks=80,
            subgoals=[SubGoal("socialise", "friendly", "",
                              lambda s: s.needs.social >= 60)],
        )

    if sim.simoleons < 200:
        return make_save_goal(500.0, priority=0.9)

    if sim.grief_stage in (1, 2):
        return Goal(
            goal_type=GoalType.MAKE_AMENDS,
            priority=0.85,
            deadline_ticks=60,
            subgoals=[SubGoal("seek comfort", "support", "",
                              lambda s: s.grief_stage <= 0)],
        )

    # Damaged reputation → repair social standing
    if sim.reputation_score < -20:
        return make_repair_goal("", priority=0.8)

    # ── 2. OCEAN personality-driven ambitions ──────────────────────────────────
    ocean: dict = sim.profile.get("ocean", {})

    # High openness → pursue mastery of a known skill
    if ocean.get("openness", 0.5) > 0.7 and random.random() < 0.30:
        skill_levels: dict = getattr(sim.skills, "levels", {})
        if skill_levels:
            # Pick the skill the sim is most invested in
            best_skill = max(skill_levels, key=lambda sk: skill_levels[sk])
        else:
            best_skill = "charisma"
        return make_mastery_goal(best_skill)

    # High conscientiousness → money management / empire building
    if ocean.get("conscientiousness", 0.5) > 0.7:
        if sim.simoleons < 500:
            return make_save_goal(500.0, priority=0.65)
        else:
            return make_empire_goal()

    # High extraversion + low reputation → chase fame
    if ocean.get("extraversion", 0.5) > 0.65 and sim.reputation_score < 30:
        return make_fame_goal()

    # High agreeableness → repair bad relationships
    if ocean.get("agreeableness", 0.5) > 0.7:
        rels = getattr(sim, "relationships", {})
        for other_id, rel in rels.items():
            friendship = rel.friendship if hasattr(rel, "friendship") else rel.get("friendship", 0)
            if friendship < -20:
                return make_repair_goal(other_id, priority=0.75)

    # High neuroticism → anxious about money
    if ocean.get("neuroticism", 0.5) > 0.65:
        return make_save_goal(max(500.0, sim.simoleons * 1.5), priority=0.7)

    # ── 3. Life-stage milestones ───────────────────────────────────────────────
    age: int = sim.profile.get("age", 25)

    if 13 <= age <= 17:  # Teen
        if random.random() < 0.40:
            if random.random() < 0.5:
                return Goal(
                    goal_type=GoalType.BUILD_FRIENDSHIP,
                    priority=0.7,
                    deadline_ticks=100,
                    subgoals=[SubGoal("make friends", "friendly", "",
                                     lambda s: s.needs.social >= 70)],
                )
            else:
                return make_skill_goal("charisma", 3, priority=0.6)

    elif 18 <= age <= 25:  # Young adult
        if random.random() < 0.30:
            if random.random() < 0.5:
                return make_fame_goal(priority=0.65)
            else:
                return make_save_goal(1000.0, priority=0.6)

    elif 26 <= age <= 59:  # Adult
        # Career momentum
        if sim.career_performance > 70 and random.random() < 0.25:
            return Goal(
                goal_type=GoalType.CAREER_ADVANCE,
                priority=0.6,
                deadline_ticks=100,
                subgoals=[SubGoal("excel at work", "intellectual", "",
                                  lambda s: s.career_performance >= 90)],
            )
        # Rival check
        rels = getattr(sim, "relationships", {})
        for other_id, rel in rels.items():
            friendship = rel.friendship if hasattr(rel, "friendship") else rel.get("friendship", 0)
            if friendship < -40:
                return make_rival_goal(other_id)

    elif age >= 60:  # Elder
        return make_legacy_goal()

    # ── 4. Relationship-state triggers ────────────────────────────────────────
    rels = getattr(sim, "relationships", {})
    married = getattr(sim, "married_to", None)
    for other_id, rel in rels.items():
        romance = rel.romance if hasattr(rel, "romance") else rel.get("romance", 0)
        if romance >= 60 and not married:
            return Goal(
                goal_type=GoalType.FIND_ROMANCE,
                target_sim_id=other_id,
                priority=0.9,
                deadline_ticks=120,
                subgoals=[SubGoal("pursue romance", "romantic", other_id,
                                  lambda s: getattr(s, "married_to", None) is not None)],
            )

    # ── 5. Career ambition fallback ────────────────────────────────────────────
    if sim.career_performance > 75 and random.random() < 0.3:
        return Goal(
            goal_type=GoalType.CAREER_ADVANCE,
            priority=0.6,
            deadline_ticks=100,
            subgoals=[SubGoal("excel at work", "intellectual", "",
                              lambda s: s.career_performance >= 90)],
        )

    return None
