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
from sim_types.enums import ControlMode
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
        self.hacker_reputation: float = 0.0
        self.wellness_state: dict = {
            "stress_level": 35.0,
            "calmness": 45.0,
            "emotional_resistance": 0.0,
            "recovery_rate": 1.0,
            "meditation_state": "relaxed",
            "focus_modifier": 1.0,
            "teleport_cooldown": 0,
        }
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
        self._action_chain: list[str] = []
        self._desire_loop: dict[str, float] = {
            "romance_push": 0.0,
            "social_repair_push": 0.0,
            "comfort_seek_push": 0.0,
        }
        self.sleep_debt: float = 0.0
        self.social_strain: float = 0.0
        # System 2b: Conversation escalation arc
        self._conversation_stage: str = (
            "small_talk"  # small_talk→teasing→disclosure→affectionate_intent
        )
        self._conversation_stage_turns: int = 0  # turns spent in current stage
        self._consent_state: dict[str, str] = {}  # partner_id → "given"|"withdrawn"|""
        # System 5: Sleep consolidation
        self._last_consolidation_tick: int = -9999
        # Extended life systems
        self.inventory: list[str] = ["snack", "book"]
        self.inventory_objects: list[dict] = []
        self.inventory_max_slots: int = 12
        self.inventory_max_weight: float = 24.0
        self.inventory_slot_limits: dict[str, int] = {
            "hand": 2,
            "body": 1,
            "utility": 9,
        }
        self.properties: list[str] = []
        self.owned_businesses: list[str] = []
        self.health_status: str = "healthy"
        self.illness_ticks_left: int = 0
        self.temperature_risk: float = 0.0
        self.internal_temperature: float = 0.0
        self.min_temp_limit: float = -85.0
        self.max_temp_limit: float = 85.0
        self.thermal_state: str = "comfortable"
        self.is_ghost: bool = False
        self.occult_type: str = "none"
        self.perk_points: int = 0
        self.perks: set[str] = set()
        self._last_perk_level_total: int = 0
        self.pending_phone_actions: list[dict] = []
        self.control_mode: ControlMode = ControlMode.AUTONOMOUS
        self.player_action_queue: list[dict] = []
        self.current_directive: dict | None = None
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
        self.career_id: str = ""  # filled by CareerManager._ensure_career
        self.career_days: int = 0  # days spent in current role
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
        self.dynasty_id: str | None = None
        self.dynasty_role: str = "member"
        # Coworkers (populated by engine init)
        self.coworker_ids: list[str] = []
        # Unlocked interactions (from milestone rewards)
        self._unlocked_interactions: list[str] = []
        self.reward_traits: set[str] = set()
        self.death_traits: set[str] = set()
        self.temporary_traits: set[str] = set()
        self.formative_traits: set[str] = set()
        self.hidden_traits: set[str] = set()
        self.trait_knowledge: dict[str, dict] = {}
        self.autonomy_profile: dict[str, float] = {}
        # Moodlet stack — always present, no engine dependency
        from core.moodlets import MoodletStack

        self.moodlets = MoodletStack()
        # Career id mapped from profile job title
        from world.careers import career_from_job_title

        if not self.career_id:
            self.career_id = career_from_job_title(profile.get("job", ""))
        self.career_days: int = random.randint(0, 20)
        # Sleep state
        self._sleeping: bool = False
        self._last_dream: dict | None = None
        self.milestones: list[dict] = []
        self.preferences: dict[str, list[str]] = {
            "activities": list(profile.get("interests", [])),
            "music": [],
            "food": [profile.get("diet", "omnivore")],
            "hobbies": list(profile.get("interests", [])),
            "decor": [],
            "personalities": list(profile.get("traits", [])),
        }
        self.refresh_trait_effects()

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
        eng = getattr(self, "_engine_ref", None)
        from persistence.ledger import TX_LIVING_COST, TX_SALARY
        if eng:
            eng._tx(self, -LIVING_COST_PER_TICK, TX_LIVING_COST,
                    description="periodic living cost")
        else:
            self.simoleons = max(0.0, self.simoleons - LIVING_COST_PER_TICK)
        if current_tick % PAY_PERIOD_TICKS == 0:
            income = BASE_SALARY.get(self.profile["income"], 90) * (
                self.career_performance / 100
            )
            if eng:
                eng._tx(self, round(income, 2), TX_SALARY,
                        counterpart=self.profile.get("job", ""),
                        description=f"salary ({self.profile.get('job','')})")
            else:
                self.simoleons += round(income, 2)
        if self.simoleons < LOW_FUNDS_THRESHOLD:
            self.emotion.add("nervousness", 0.5, duration=5, source="financial stress")

    def tick(self, wants_engine: "WantsEngine", all_sim_ids: list[str]) -> None:
        if self.control_mode == ControlMode.INTERRUPTED:
            self.emotion.add("apprehensive", 0.2, duration=1, source="interrupted")
            self.control_mode = ControlMode.AUTONOMOUS

        self.needs.tick(self.ocean)
        self._apply_trait_need_decay_mods()
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
        mood_mod *= self._trait_career_multiplier()
        self.career_performance = max(
            0, min(100, self.career_performance + random.uniform(-1, 1) + mood_mod)
        )

        # Health scare counter
        from datasets.health import HEALTH_SCARE_ENERGY_THRESHOLD

        if self.needs.energy < HEALTH_SCARE_ENERGY_THRESHOLD:
            self._low_energy_ticks += 1
        else:
            self._low_energy_ticks = 0

        self.sleep_debt = max(
            0.0, min(100.0, self.sleep_debt + max(0.0, 50.0 - self.needs.energy) * 0.02)
        )
        self.social_strain = max(
            0.0,
            min(100.0, self.social_strain + max(0.0, 45.0 - self.needs.social) * 0.03),
        )
        self._desire_loop["romance_push"] = max(
            0.0,
            min(
                1.0,
                (0.2 if self.emotion.dominant == "desire" else 0.0)
                + (0.2 if "romantic" in self.profile.get("traits", []) else 0.0)
                + (0.1 if self.needs.fun < 45 else 0.0),
            ),
        )
        self._desire_loop["social_repair_push"] = max(
            0.0, min(1.0, self.social_strain / 100.0)
        )
        self._desire_loop["comfort_seek_push"] = max(
            0.0, min(1.0, self.sleep_debt / 100.0)
        )

        self.economy_tick(getattr(self, "_current_tick", 0))

    def want_pressure_toward(self, other_sim_id: str) -> float:
        social_pressure = self.needs.pressure_vector().get("social", 0)
        want_bonus = sum(
            w.priority for w in self.active_wants if w.target_sim == other_sim_id
        )
        extraversion_bonus = self.ocean["extraversion"] * 0.3
        social_bias = self.autonomy_profile.get("social", 0.0) * 0.15
        solitude_bias = self.autonomy_profile.get("solitude", 0.0) * 0.12
        return round(
            min(
                1.0,
                social_pressure * 0.5
                + want_bonus
                + extraversion_bonus
                + social_bias
                - solitude_bias,
            ),
            3,
        )

    def refresh_trait_effects(self) -> None:
        from core.traits import derive_autonomy_profile

        self.autonomy_profile = derive_autonomy_profile(self)

    def add_trait(self, trait_id: str, source: str = "temporary") -> None:
        if source == "reward":
            self.reward_traits.add(trait_id)
        elif source == "death":
            self.death_traits.add(trait_id)
        elif source == "formative":
            self.formative_traits.add(trait_id)
        else:
            self.temporary_traits.add(trait_id)
        self.refresh_trait_effects()

    def remove_trait(self, trait_id: str) -> None:
        for bucket in (
            self.reward_traits,
            self.death_traits,
            self.temporary_traits,
            self.formative_traits,
        ):
            bucket.discard(trait_id)
        self.refresh_trait_effects()

    def _trait_career_multiplier(self) -> float:
        from core.traits import career_performance_multiplier

        return career_performance_multiplier(self)

    def _apply_trait_need_decay_mods(self) -> None:
        from core.traits import active_traits, TRAIT_DEFS

        for trait in active_traits(self):
            tdef = TRAIT_DEFS.get(trait)
            if not tdef:
                continue
            for need, mult in tdef.need_decay_mods.items():
                if hasattr(self.needs, need):
                    current = float(getattr(self.needs, need))
                    adjusted = current + (current - 50.0) * (1.0 - float(mult)) * 0.01
                    setattr(self.needs, need, max(0.0, min(100.0, adjusted)))
