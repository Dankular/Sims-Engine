"""
world/career_manager.py — Career progression, promotions, and workplace events.
"""
from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

from world.careers import CAREER_CATALOGUE, CareerDef, CareerLevel, career_from_job_title

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)

# How often (in ticks) the career system ticks for each sim
CAREER_TICK_INTERVAL = 5
# Probability of a promotion check happening on a given career tick
PROMOTION_CHECK_CHANCE = 0.15
# Probability of a chance card event per career tick
CHANCE_CARD_CHANCE = 0.08
# Days-in-role tracking: 1 career tick = 1 "day" for tracking purposes
PERFORMANCE_DECAY_RATE = 0.5   # performance drifts toward 50 slightly each tick
SKILL_PERF_BONUS = 2.0          # bonus performance per skill-level above requirement


CHANCE_CARDS: list[dict] = [
    {
        "id": "lucky_client",
        "text": "A big client requests {name} personally.",
        "outcomes": {"accept": (15.0, 300), "decline": (-5.0, 0)},
    },
    {
        "id": "overtime",
        "text": "{name} is asked to work overtime.",
        "outcomes": {"agree": (10.0, 150), "refuse": (-8.0, 0)},
    },
    {
        "id": "office_conflict",
        "text": "A coworker picks a fight with {name}.",
        "outcomes": {"stand_firm": (-5.0, 0), "smooth_it_over": (5.0, 0)},
    },
    {
        "id": "public_recognition",
        "text": "{name}'s work earns public praise.",
        "outcomes": {"accept_award": (12.0, 100), "deflect": (3.0, 50)},
    },
    {
        "id": "ethical_dilemma",
        "text": "{name} discovers something questionable at work.",
        "outcomes": {"report_it": (-10.0, 0), "ignore": (5.0, 0)},
    },
    {
        "id": "opportunity",
        "text": "A recruiter approaches {name} with a competing offer.",
        "outcomes": {"negotiate_raise": (8.0, 200), "stay_loyal": (5.0, 100)},
    },
    {
        "id": "mentor",
        "text": "A senior colleague offers to mentor {name}.",
        "outcomes": {"accept": (15.0, 0), "decline": (0.0, 0)},
    },
    {
        "id": "workplace_scandal",
        "text": "Rumors swirl about misconduct near {name}.",
        "outcomes": {"distance_yourself": (-3.0, 0), "support_accused": (-10.0, 0)},
    },
]


class CareerManager:

    def __init__(self) -> None:
        self._sim_days: dict[str, int] = {}  # sim_id → days in current role

    def tick(self, engine: "SimEngine") -> None:
        if engine.tick_count % CAREER_TICK_INTERVAL != 0:
            return
        for sim in engine.sims:
            if getattr(sim, "_sleeping", False):
                continue
            self._ensure_career(sim)
            self._tick_performance(sim)
            if random.random() < PROMOTION_CHECK_CHANCE:
                self._check_promotion(sim, engine)
            if random.random() < CHANCE_CARD_CHANCE:
                self._fire_chance_card(sim, engine)
            self._pay_salary(sim, engine)

    # ── Career assignment ────────────────────────────────────────────────────────

    def _ensure_career(self, sim: "Sim") -> None:
        if not getattr(sim, "career_id", None):
            job_title = sim.profile.get("job", "")
            sim.career_id = career_from_job_title(job_title)
        if not getattr(sim, "career_days", None):
            sim.career_days = random.randint(0, 20)

    def assign_career(self, sim: "Sim", career_id: str, level: int = 1, branch: str = "base") -> None:
        sim.career_id = career_id
        sim.career_level = level
        sim.career_branch = branch
        sim.career_performance = 50.0
        sim.career_days = 0
        logger.info("[Career] %s assigned to %s level %d", sim.name, career_id, level)

    def switch_career(self, sim: "Sim", new_career_id: str) -> None:
        old = getattr(sim, "career_id", "unemployed")
        self.assign_career(sim, new_career_id, level=1, branch="base")
        logger.info("[Career] %s switched from %s to %s", sim.name, old, new_career_id)
        if hasattr(sim, "moodlets"):
            sim.moodlets.add("stressed", source="career_switch")

    # ── Performance ─────────────────────────────────────────────────────────────

    def _tick_performance(self, sim: "Sim") -> None:
        level_def = self._get_level_def(sim)
        if level_def is None:
            return

        # Drift toward 50
        delta = (50.0 - sim.career_performance) * 0.02

        # Mood contribution
        mood = sim.emotion.dominant_valence
        delta += (mood - 0.5) * 4.0

        # Skill bonus: sum skill levels above threshold
        for skill, req_level in level_def.promotion_req.skills.items():
            actual = sim.skills.levels.get(skill, 0.0)
            if actual > req_level:
                delta += (actual - req_level) * SKILL_PERF_BONUS * 0.1

        # Moodlet bonus
        if hasattr(sim, "moodlets"):
            mv = sim.moodlets.net_valence()
            delta += mv * 3.0

        sim.career_performance = max(0.0, min(100.0, sim.career_performance + delta + random.uniform(-1.5, 1.5)))
        sim.career_days = getattr(sim, "career_days", 0) + 1

    # ── Promotions & Demotions ────────────────────────────────────────────────────

    def _check_promotion(self, sim: "Sim", engine: "SimEngine") -> None:
        career = CAREER_CATALOGUE.get(getattr(sim, "career_id", "unemployed"))
        if not career or career.category == "unemployed":
            return

        level_def = self._get_level_def(sim)
        if level_def is None:
            return

        req = level_def.promotion_req
        current_level = sim.career_level
        max_lv = career.max_level()

        if current_level >= max_lv:
            return

        # Check requirements
        if sim.career_performance < req.performance:
            return
        if sim.career_days < req.days_in_role:
            return
        if sim.reputation_score < req.reputation:
            return
        for skill, need in req.skills.items():
            if sim.skills.levels.get(skill, 0.0) < need:
                return
        # Friendship check (coworkers)
        if req.friendship_count > 0:
            coworkers = getattr(sim, "coworker_ids", [])
            friends = sum(
                1 for cid in coworkers
                if engine.relationships.get(sim.sim_id, cid).friendship >= 40
            )
            if friends < req.friendship_count:
                return

        # Promote
        # Branch selection at level 5
        new_level = current_level + 1
        new_branch = sim.career_branch
        if new_level == 6 and len(career.branches) > 1 and sim.career_branch == "base":
            new_branch = random.choice(list(career.branches.keys()))
            logger.info("[Career] %s branching into %s", sim.name, career.branches[new_branch])

        sim.career_level = new_level
        sim.career_branch = new_branch
        sim.career_days = 0
        sim.career_performance = max(50.0, sim.career_performance - 10)  # reset bar slightly

        if hasattr(sim, "moodlets"):
            sim.moodlets.add("just_promoted", source="career_promotion")
            sim.moodlets.add("feeling_confident", source="career_promotion")

        # Salary bump
        new_def = self._get_level_def(sim)
        sim.simoleons += new_def.salary_per_tick * 10 if new_def else 0  # bonus pay

        logger.info("[Career] %s promoted to level %d (%s) in %s",
                    sim.name, new_level, new_branch, getattr(sim, "career_id", "?"))

        # Fire event
        try:
            from narrative.event_record import LifeEvent, EventType
            import uuid
            ev = LifeEvent(
                event_id=str(uuid.uuid4()),
                event_type=EventType.PROMOTION,
                primary_sim_id=sim.sim_id,
                secondary_sim_ids=[],
                narrative=f"{sim.name} was promoted to {self._get_title(sim)} at work.",
                tick=engine.tick_count,
                visibility="public",
                known_to={sim.sim_id},
                valence=0.8,
                intensity=0.7,
                duration_ticks=8,
                expires_tick=engine.tick_count + 8,
                consequences=None,
            )
            engine.event_engine.process(ev, engine)
        except Exception:
            pass

    def _check_demotion(self, sim: "Sim", engine: "SimEngine") -> None:
        if sim.career_performance < 25.0 and random.random() < 0.20:
            if sim.career_level > 1:
                sim.career_level -= 1
                sim.career_performance = 50.0
                if hasattr(sim, "moodlets"):
                    sim.moodlets.add("crushed_dreams", source="demotion")
                    sim.moodlets.add("stressed", source="demotion")
                logger.info("[Career] %s demoted to level %d", sim.name, sim.career_level)

    # ── Chance Cards ─────────────────────────────────────────────────────────────

    def _fire_chance_card(self, sim: "Sim", engine: "SimEngine") -> None:
        card = random.choice(CHANCE_CARDS)
        # Auto-resolve: pick best outcome based on personality
        ocean = sim.profile.get("ocean", {})
        outcomes = list(card["outcomes"].items())
        # Agreeable sims pick peaceful options; open sims take risks
        if ocean.get("agreeableness", 0.5) > 0.6:
            choice_label, (perf_delta, money) = outcomes[0]
        elif ocean.get("neuroticism", 0.5) > 0.6:
            choice_label, (perf_delta, money) = min(outcomes, key=lambda x: abs(x[1][0]))
        else:
            choice_label, (perf_delta, money) = random.choice(outcomes)

        sim.career_performance = max(0.0, min(100.0, sim.career_performance + perf_delta))
        sim.simoleons = max(0.0, sim.simoleons + money)

        narrative = card["text"].format(name=sim.name) + f" [{choice_label}: Δperf={perf_delta:+.0f}]"
        logger.debug("[ChanceCard] %s", narrative)

        if perf_delta > 10 and hasattr(sim, "moodlets"):
            sim.moodlets.add("proud", source="chance_card")
        elif perf_delta < -5 and hasattr(sim, "moodlets"):
            sim.moodlets.add("stressed", source="chance_card")

        # Check demotion after bad cards
        if sim.career_performance < 25:
            self._check_demotion(sim, engine)

    # ── Salary ───────────────────────────────────────────────────────────────────

    def _pay_salary(self, sim: "Sim", engine: "SimEngine") -> None:
        level_def = self._get_level_def(sim)
        if level_def and level_def.salary_per_tick > 0:
            bonus = 1.0 + (sim.career_performance - 50.0) / 200.0
            sim.simoleons += round(level_def.salary_per_tick * bonus, 2)

    # ── Helpers ──────────────────────────────────────────────────────────────────

    def _get_level_def(self, sim: "Sim") -> CareerLevel | None:
        career = CAREER_CATALOGUE.get(getattr(sim, "career_id", "unemployed"))
        if not career:
            return None
        return career.get_level(sim.career_level, getattr(sim, "career_branch", "base"))

    def _get_title(self, sim: "Sim") -> str:
        level_def = self._get_level_def(sim)
        return level_def.title if level_def else "Unknown"

    def career_summary(self, sim: "Sim") -> dict:
        career = CAREER_CATALOGUE.get(getattr(sim, "career_id", "unemployed"))
        level_def = self._get_level_def(sim)
        return {
            "career_id": getattr(sim, "career_id", "unemployed"),
            "career_name": career.name if career else "Unemployed",
            "title": level_def.title if level_def else "Unemployed",
            "level": sim.career_level,
            "branch": getattr(sim, "career_branch", "base"),
            "branch_name": career.branches.get(sim.career_branch, "Base") if career else "",
            "performance": round(sim.career_performance, 1),
            "days_in_role": getattr(sim, "career_days", 0),
            "salary_per_tick": round(level_def.salary_per_tick, 2) if level_def else 0,
            "max_level": career.max_level() if career else 1,
            "schedule": career.schedule if career else "flexible",
            "category": career.category if career else "unemployed",
        }
