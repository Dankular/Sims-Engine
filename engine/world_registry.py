"""
engine/world_registry.py — Thread-safe cache of remote sim states + stub proxy.

WorldRegistry tracks every sim visible on the NATS network (keyed by room).
RemoteSimStub wraps a state dict so that pick_interaction_pair() and
choose_interaction() treat remote sims identically to local Sim objects.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sim_types.enums import LODTier


class RemoteSimStub:
    """
    Lightweight proxy for a remote sim.

    Implements the subset of the Sim interface required by:
      - pick_interaction_pair()  → needs.pressure_vector(), want_pressure_toward(), sim_id
      - choose_interaction()     → ocean, profile, emotion, skills, fears, is_on_cooldown()
    All write operations (needs.restore, skills.gain_xp, etc.) are no-ops — the
    owning client is responsible for applying deltas to the real Sim object.
    """

    def __init__(self, state: dict) -> None:
        from sim_types.enums import LODTier

        self.sim_id: str = state["id"]
        self.name: str = state["name"]
        self.lod_tier = LODTier.ACTIVE
        self.simoleons: float = float(state.get("simoleons", 100.0))
        self.career_performance: float = float(state.get("career_performance", 50.0))
        self.household_id: str | None = state.get("household_id")
        self.reputation_score: float = float(state.get("reputation_score", 0.0))
        self.ei_reputation: float = float(state.get("ei_reputation", 0.0))
        self.social_orientation: str = state.get("social_orientation", "Warm-Agreeable")
        self.fears: list = state.get("fears", [])
        self.active_wants: list = []
        self.trauma_events: list = []
        self.grief_stage: int = -1
        self.grief_target: str = ""
        self._social_drought_ticks: int = 0
        self._burnout_active: bool = False
        self._sleeping: bool = False
        self._current_venue_name: str = ""
        self._dialogue_buffer: list = []
        self._dialogue_partner: str = ""
        self._dialogue_last_tick: int = -999
        self._low_energy_ticks: int = 0
        self._action_cooldowns: dict[str, int] = {}
        self._active_goal = None
        self._want_refresh_countdown = 0
        self.reward_traits = set(state.get("reward_traits", []))
        self.death_traits = set(state.get("death_traits", []))
        self.temporary_traits = set(state.get("temporary_traits", []))
        self.formative_traits = set(state.get("formative_traits", []))
        self.autonomy_profile = dict(state.get("autonomy_profile", {}))
        self.trait_knowledge = {}

        # Emotion proxy
        self.emotion = SimpleNamespace(
            dominant=state.get("dominant_emotion", "neutral"),
            dominant_valence=float(state.get("dominant_valence", 0.0)),
        )
        # EmotionState.add — no-op for stub
        self.emotion.add = lambda *a, **kw: None
        self.emotion.tick = lambda ocean: None

        # Needs proxy — mirrors the Needs fields
        nd = state.get("needs", {})
        self.needs = SimpleNamespace(
            hunger=float(nd.get("hunger", 50.0)),
            energy=float(nd.get("energy", 50.0)),
            social=float(nd.get("social", 50.0)),
            fun=float(nd.get("fun", 50.0)),
            hygiene=float(nd.get("hygiene", 50.0)),
            environment=float(nd.get("environment", 50.0)),
            bladder=float(nd.get("bladder", 50.0)),
            comfort=float(nd.get("comfort", 50.0)),
        )
        self.needs.restore = lambda need, amount: None
        self.needs.tick = lambda ocean: None
        self.needs.pressure_vector = self._pressure_vector

        # Skills proxy
        sd = state.get("skills", {})
        self.skills = SimpleNamespace(levels=dict(sd))
        self.skills.gain_xp = lambda skill, xp: None

        # Profile — reconstruct all fields choose_interaction() may read
        self.profile = {
            "id": self.sim_id,
            "name": self.name,
            "job": state.get("job", ""),
            "age": int(state.get("age", 25)),
            "gender": state.get("gender", ""),
            "traits": list(state.get("traits", [])),
            "dealbreakers": list(state.get("dealbreakers", [])),
            "aspiration": state.get("aspiration", ""),
            "humor_type": state.get("humor_type", ""),
            "comm_style": state.get("comm_style", ""),
            "attachment": state.get("attachment", ""),
            "interests": list(state.get("interests", [])),
            "mbti": state.get("mbti", ""),
            "zodiac": state.get("zodiac", ""),
            "ocean": dict(
                state.get(
                    "ocean",
                    {
                        "openness": 0.5,
                        "conscientiousness": 0.5,
                        "extraversion": 0.5,
                        "agreeableness": 0.5,
                        "neuroticism": 0.5,
                    },
                )
            ),
            "parent_ids": list(state.get("parent_ids", [])),
        }

    # ── Properties that mirror Sim ─────────────────────────────────────────────

    @property
    def ocean(self) -> dict:
        return self.profile["ocean"]

    @property
    def parent_ids(self) -> list:
        return self.profile.get("parent_ids", [])

    @property
    def is_child_of(self) -> bool:
        return len(self.parent_ids) > 0

    # ── Methods required by scheduler ─────────────────────────────────────────

    def _pressure_vector(self) -> dict:
        max_val = 100.0
        return {
            "hunger": max(0.0, (max_val - self.needs.hunger) / max_val),
            "energy": max(0.0, (max_val - self.needs.energy) / max_val),
            "social": max(0.0, (max_val - self.needs.social) / max_val),
            "fun": max(0.0, (max_val - self.needs.fun) / max_val),
            "bladder": max(0.0, (max_val - self.needs.bladder) / max_val),
            "hygiene": max(0.0, (max_val - self.needs.hygiene) / max_val),
            "environment": max(0.0, (max_val - self.needs.environment) / max_val),
            "comfort": max(0.0, (max_val - self.needs.comfort) / max_val),
        }

    def want_pressure_toward(self, other_sim_id: str) -> float:
        social_pressure = self._pressure_vector().get("social", 0.0)
        extraversion_bonus = self.ocean["extraversion"] * 0.3
        return round(min(1.0, social_pressure * 0.5 + extraversion_bonus), 3)

    def is_on_cooldown(self, action: str, current_tick: int) -> bool:
        from config import COOLDOWN_TICKS

        return current_tick - self._action_cooldowns.get(action, -9999) < COOLDOWN_TICKS

    def register_action(self, action: str, current_tick: int) -> None:
        self._action_cooldowns[action] = current_tick

    def schedule_phase(self, hour: int) -> str:
        return "social"

    def economy_tick(self, tick: int) -> None:
        pass

    def tick(self, wants_engine, all_sim_ids: list) -> None:
        pass


# ── WorldRegistry ──────────────────────────────────────────────────────────────


class WorldRegistry:
    """
    Thread-safe cache of remote sim states, organised by room.

    Engine thread calls get_room_stubs() each tick to discover who to interact
    with. NATS thread calls update_states() whenever a state broadcast arrives.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sims: dict[str, dict] = {}  # sim_id → state dict
        self._client_of: dict[str, str] = {}  # sim_id → client_id
        self._room_members: dict[str, set[str]] = {}  # room_id → {sim_id}

    def update_states(self, client_id: str, room_id: str, states: list[dict]) -> None:
        with self._lock:
            room = self._room_members.setdefault(room_id, set())
            for s in states:
                sid = s.get("id", "")
                if not sid:
                    continue
                self._sims[sid] = s
                self._client_of[sid] = client_id
                room.add(sid)

    def get_room_stubs(
        self, room_id: str, exclude_ids: set[str]
    ) -> list[RemoteSimStub]:
        with self._lock:
            members = self._room_members.get(room_id, set())
            return [
                RemoteSimStub(self._sims[sid])
                for sid in members
                if sid not in exclude_ids and sid in self._sims
            ]

    def get_client_for_sim(self, sim_id: str) -> str | None:
        with self._lock:
            return self._client_of.get(sim_id)

    def make_stub(self, state: dict) -> RemoteSimStub:
        return RemoteSimStub(state)

    def all_states(self) -> list[dict]:
        with self._lock:
            return list(self._sims.values())

    def remove_client(self, client_id: str) -> None:
        with self._lock:
            gone = [sid for sid, cid in self._client_of.items() if cid == client_id]
            for sid in gone:
                self._sims.pop(sid, None)
                self._client_of.pop(sid, None)
            for members in self._room_members.values():
                members -= set(gone)
