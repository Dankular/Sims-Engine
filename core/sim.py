from __future__ import annotations

import random
from typing import Optional, TYPE_CHECKING

from config import (
    BASE_SALARY,
    COOLDOWN_TICKS,
    FEAR_REDUCTION,
    LIVING_COST_PER_TICK,
    LOW_FUNDS_THRESHOLD,
    PAY_PERIOD_TICKS,
)
from core.emotions import EmotionState
from core.needs import Needs
from core.skills import SkillsSystem
from sim_types.enums import LODTier
from sim_types.sim_types import Fear, Want
from config import SCHEDULE_SOCIAL, SCHEDULE_WORK

if TYPE_CHECKING:
    from core.wants import WantsEngine


def resolve_fears(sim: "Sim", valence: float) -> list[str]:
    resolved = []
    for fear in list(sim.fears):
        fear.severity = max(0.0, fear.severity - FEAR_REDUCTION * valence)
        if fear.severity < 0.1:
            sim.fears.remove(fear)
            resolved.append(fear.label)
            sim.emotion.add("relief", 0.6, duration=6, source=f"overcame {fear.label}")
    return resolved


class Sim:
    def __init__(self, profile: dict):
        self.sim_id = profile["id"]
        self.name = profile["name"]
        self.profile = profile
        self.needs = Needs()
        self.emotion = EmotionState()
        self.skills = SkillsSystem(profile)
        self.fears: list[Fear] = []
        self.active_wants: list[Want] = []
        self._want_refresh_countdown = 0
        self.career_performance = 50.0 + random.uniform(-10, 10)
        self.simoleons = random.uniform(800, 5000)
        self._action_cooldowns: dict[str, int] = {}
        self._current_tick: int = 0
        self.lod_tier: LODTier = LODTier.ACTIVE
        self.household_id: Optional[str] = None
        # Reputation system (Class 1)
        self.reputation_score: float = 0.0  # -100..100; YTA events lower it
        # Social orientation (Class 2) — circumplex theory
        self.social_orientation: str = "Warm-Agreeable"
        # Emotional intelligence reputation (Class 7)
        self.ei_reputation: float = 0.0  # -50..50
        # Creative reputation (Gap 3)
        self.creative_reputation: float = 0.0  # 0..100
        # Health scare tracking
        self._low_energy_ticks: int = 0
        # Arc systems (core/arcs.py)
        self.grief_stage: int = -1  # -1 = not grieving
        self.grief_target: str = ""
        self._grief_tick_count: int = 0
        self._social_drought_ticks: int = 0
        self._high_perf_low_energy_ticks: int = 0
        self._burnout_active: bool = False
        self._burnout_recovery_ticks: int = 0
        self.trauma_events: list[str] = []
        self.action_history: dict[str, int] = {}
        # System 4: Goal/intent persistence
        self._active_goal = None  # Optional[SimGoal] — lazy import avoids circular
        # System 2: Dialogue buffer (working memory)
        self._dialogue_buffer: list[dict] = []  # last N turns with current partner
        self._dialogue_partner: str = ""  # sim_id of current conversation partner
        self._dialogue_last_tick: int = -999
        # System 5: Sleep consolidation
        self._last_consolidation_tick: int = -9999
        # Extended life systems
        self.inventory: list[str] = ["snack", "book"]
        self.properties: list[str] = []
        self.owned_businesses: list[str] = []
        self.health_status: str = "healthy"
        self.illness_ticks_left: int = 0
        self.temperature_risk: float = 0.0
        self.is_ghost: bool = False
        self.occult_type: str = "none"
        self.perk_points: int = 0
        self.perks: set[str] = set()
        self._last_perk_level_total: int = 0
        self.pending_phone_actions: list[dict] = []
        self.active_gig: dict | None = None
        self.active_odd_job: dict | None = None
        self.odd_job_reputation: float = 0.0
        self.pending_invitations: list[dict] = []
        self.hazard_flags: dict[str, float] = {
            "fire": 0.0,
            "electrocution": 0.0,
            "starvation": 0.0,
        }
        self._starvation_ticks: int = 0
        self._near_fire_ticks: int = 0
        self._drowning_ticks: int = 0
        self.school_performance: float = 50.0 if profile.get("age", 0) <= 17 else 0.0
        self.homework_progress: float = 0.0
        self.scholarship_points: float = 0.0
        self.university_readiness: float = 0.0
        self.career_level: int = 1
        self.career_branch: str = "base"
        self.work_from_home_task: dict | None = None
        self.occult_power: float = 0.0
        self.university_status: str = "none"
        self.degree_track: str = ""
        self.degree_progress: float = 0.0
        self.occult_perks: list[str] = []
        self.occult_weaknesses: list[str] = []
        self.pet_ids: list[str] = []
        self.travel_history: list[str] = []
        # Marriage / relationship status
        self._married_to: str | None = None
        self._divorce_risk_ticks: int = 0
        # Fame / celebrity
        self.celebrity_score: float = 0.0
        self.celebrity_tier: str = "none"
        # Lifetime wish
        from core.lifetime_wish import generate_wish

        self.lifetime_wish = generate_wish(self.profile.get("aspiration", "Fortune"))
        # Aspiration milestones
        from core.aspiration_rewards import generate_progress

        self.aspiration_progress = generate_progress(
            self.profile.get("aspiration", "Fortune")
        )
        # Club memberships (populated by ClubManager)
        self.club_ids: list[str] = []
        # Coworkers (populated by engine init)
        self.coworker_ids: list[str] = []
        # Unlocked interactions (from milestone rewards)
        self._unlocked_interactions: list[str] = []

    @property
    def ocean(self) -> dict:
        return self.profile["ocean"]

    @property
    def parent_ids(self) -> list[str]:
        return self.profile.get("parent_ids", [])

    @property
    def is_child_of(self) -> bool:
        return len(self.parent_ids) > 0

    def is_on_cooldown(self, action: str, current_tick: int) -> bool:
        return current_tick - self._action_cooldowns.get(action, -999) < COOLDOWN_TICKS

    def register_action(self, action: str, current_tick: int) -> None:
        self._action_cooldowns[action] = current_tick

    def schedule_phase(self, hour: int) -> str:
        if hour in SCHEDULE_WORK:
            return "work"
        if hour in SCHEDULE_SOCIAL:
            return "social"
        return "home"

    def economy_tick(self, current_tick: int) -> None:
        self.simoleons = max(0.0, self.simoleons - LIVING_COST_PER_TICK)
        if current_tick % PAY_PERIOD_TICKS == 0:
            income = BASE_SALARY.get(self.profile["income"], 90) * (
                self.career_performance / 100
            )
            self.simoleons += round(income, 2)
        if self.simoleons < LOW_FUNDS_THRESHOLD:
            self.emotion.add("nervousness", 0.5, duration=5, source="financial stress")

    def tick(self, wants_engine: "WantsEngine", all_sim_ids: list[str]) -> None:
        self.needs.tick(self.ocean)
        self.emotion.tick(self.ocean)
        # Update social orientation from current needs + emotion
        try:
            from datasets.social_orientation import orientation_from_ocean_needs

            needs_dict = {
                n: getattr(self.needs, n) for n in ["energy", "social", "fun", "hunger"]
            }
            self.social_orientation = orientation_from_ocean_needs(
                self.ocean, needs_dict, self.emotion.dominant
            )
        except Exception:
            pass
        self._want_refresh_countdown -= 1
        if self._want_refresh_countdown <= 0:
            self.active_wants = wants_engine.generate(self, all_sim_ids)
            self._want_refresh_countdown = 8

        for need in self.needs.critical_needs():
            label = "annoyance" if need in ("bladder", "hunger") else "discomfort"
            self.emotion.add(label, 0.7, duration=3, source=f"critical {need}")

        mood_mod = (self.emotion.dominant_valence - 0.5) * 0.5
        self.career_performance = max(
            0, min(100, self.career_performance + random.uniform(-1, 1) + mood_mod)
        )

        # Health scare counter
        from datasets.health import HEALTH_SCARE_ENERGY_THRESHOLD

        if self.needs.energy < HEALTH_SCARE_ENERGY_THRESHOLD:
            self._low_energy_ticks += 1
        else:
            self._low_energy_ticks = 0

        self.economy_tick(getattr(self, "_current_tick", 0))

    def want_pressure_toward(self, other_sim_id: str) -> float:
        social_pressure = self.needs.pressure_vector().get("social", 0)
        want_bonus = sum(
            w.priority for w in self.active_wants if w.target_sim == other_sim_id
        )
        extraversion_bonus = self.ocean["extraversion"] * 0.3
        return round(
            min(1.0, social_pressure * 0.5 + want_bonus + extraversion_bonus), 3
        )
