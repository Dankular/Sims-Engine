"""
world/grim_reaper.py — Grim Reaper NPC.

Mechanics (extracted from The Sims 2/3/4):
  • Arrives when any Sim dies; reaps their soul (ghost + tombstone).
  • Linger chance: 20% base + 5% per death this lot visit, cap 75%.
  • While lingering: Sims can socialize, plead, or challenge him to chess.
  • Plead: friendship + charisma gated, base 20%, max ~65%.
  • Chess: logic-skill gated, base 30%, max ~80%. Failure = still dead.
  • Pet save: best-friend pet, 35% base ± trait modifiers, once per Sim.
  • Personality: at least one mean/evil trait; cold to Sims, kind to pets.
  • Departs after LINGER_TICKS ticks or when forcibly dismissed.
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim

# ── Constants ─────────────────────────────────────────────────────────────────

LINGER_BASE_CHANCE    = 0.20
LINGER_PER_DEATH      = 0.05
LINGER_MAX            = 0.75
LINGER_TICKS          = 12        # how long Grim sticks around

PLEAD_BASE_CHANCE     = 0.20
PLEAD_FRIENDSHIP_MAX  = 0.30      # +30% at friendship=100
PLEAD_CHARISMA_PER    = 0.03      # +3% per charisma level
PLEAD_MAX             = 0.65

CHESS_BASE_CHANCE     = 0.30
CHESS_LOGIC_PER       = 0.05      # +5% per logic level
CHESS_MAX             = 0.80
CHESS_MIN             = 0.05

PET_SAVE_BASE         = 0.35
PET_SAVE_BRAVE_BONUS  = 0.10
PET_SAVE_COWARD_MALUS = 0.10
PET_BRAVE_TRAITS      = {"aggressive", "loyal", "brave", "feisty", "bold"}
PET_COWARD_TRAITS     = {"clueless", "lazy", "nervous", "skittish", "timid"}

# Cause → ghost death-trait
_CAUSE_TRAIT: dict[str, str] = {
    "fire":          "fire_affinity",
    "electrocution": "electric_aura",
    "drowning":      "cold_affinity",
    "starvation":    "haunting_presence",
    "old_age":       "peaceful_spirit",
    "emotion":       "restless_spirit",
    "illness":       "mournful_shade",
    "burnout":       "restless_spirit",
}

# Interactions Grim makes available to Sims while lingering
GRIM_SOCIAL_INTERACTIONS = [
    "ask grim about death",
    "chat with the grim reaper",
    "beg for mercy",
    "try to make grim laugh",
    "offer grim a drink",
]


# ── GrimReaperNPC ─────────────────────────────────────────────────────────────

class GrimReaperNPC:
    def __init__(self) -> None:
        self.is_present:       bool = False
        self.lot_id:           str  = ""
        self._arrive_tick:     int  = -1
        self._deaths_this_visit: int = 0
        self._linger:          bool = False
        self._linger_ticks_left: int = 0

        # Tombstones: list[{"sim_id", "sim_name", "cause", "lot_id", "tick"}]
        self.tombstones: list[dict] = []

        # Track which pets have already saved someone (once per Sim lifetime)
        self._pet_saves_used: set[str] = set()   # pet_sim_id → exhausted

        # Grim's own personality (generated once)
        self.personality = _generate_grim_personality()

    # ── Main entry: a Sim just died ───────────────────────────────────────────

    def on_sim_death(
        self,
        dead_sim: "Sim",
        cause: str,
        lot_id: str,
        tick: int,
    ) -> dict:
        """
        Called by the engine when a Sim is processed for death.
        Returns a result dict with ghost_trait, tombstone, and linger decision.
        """
        ghost_trait = _CAUSE_TRAIT.get(cause, "haunting_presence")

        # Tombstone record
        stone = {
            "sim_id":   dead_sim.sim_id,
            "sim_name": dead_sim.name,
            "cause":    cause,
            "lot_id":   lot_id,
            "tick":     tick,
            "ghost_trait": ghost_trait,
        }
        self.tombstones.append(stone)

        # Arrival / linger logic
        if not self.is_present or self.lot_id != lot_id:
            self._deaths_this_visit = 0

        self._deaths_this_visit += 1
        linger_chance = min(
            LINGER_MAX,
            LINGER_BASE_CHANCE + LINGER_PER_DEATH * self._deaths_this_visit,
        )
        self._linger = random.random() < linger_chance

        self.is_present = True
        self.lot_id     = lot_id
        self._arrive_tick = tick
        self._linger_ticks_left = LINGER_TICKS if self._linger else 1

        return {
            "ghost_trait":    ghost_trait,
            "tombstone":      stone,
            "grim_lingering": self._linger,
            "linger_chance":  round(linger_chance, 2),
        }

    # ── Interactions ─────────────────────────────────────────────────────────

    def attempt_plead(self, pleading_sim: "Sim", dying_sim: "Sim") -> dict:
        """
        A Sim pleads with Grim to spare dying_sim.
        friendship + charisma determine success probability.
        """
        if not self.is_present:
            return {"ok": False, "reason": "grim_not_present"}

        rel = None
        try:
            from core.relationships import RelationshipGraph
            # Caller should pass the relationship record; we derive from sim attrs
        except Exception:
            pass

        friendship = 0.0
        try:
            from engine.engine import _current_engine
            if _current_engine:
                rec = _current_engine.relationships.get(
                    pleading_sim.sim_id, dying_sim.sim_id
                )
                friendship = rec.friendship
        except Exception:
            pass

        charisma = int(pleading_sim.skills.levels.get("charisma", 0))
        rep_penalty = 0.10 if pleading_sim.reputation_score < -20 else 0.0

        chance = min(
            PLEAD_MAX,
            PLEAD_BASE_CHANCE
            + (max(0.0, friendship) / 100.0) * PLEAD_FRIENDSHIP_MAX
            + charisma * PLEAD_CHARISMA_PER
            - rep_penalty,
        )
        success = random.random() < chance

        if success:
            pleading_sim.emotion.add(
                "joy", 0.8, duration=8, source="plead:grim_spared"
            )
            return {
                "ok": True,
                "success": True,
                "chance": round(chance, 2),
                "message": "Grim hesitates, then grudgingly returns the soul.",
            }
        else:
            pleading_sim.emotion.add(
                "sadness", 0.7, duration=6, source="plead:grim_refused"
            )
            return {
                "ok": True,
                "success": False,
                "chance": round(chance, 2),
                "message": "Grim shakes his head coldly and continues the reaping.",
            }

    def attempt_chess(self, challenger: "Sim") -> dict:
        """Challenge Grim to chess. Logic skill governs success probability."""
        if not self.is_present:
            return {"ok": False, "reason": "grim_not_present"}

        logic = int(challenger.skills.levels.get("logic", 0))
        chance = max(
            CHESS_MIN,
            min(CHESS_MAX, CHESS_BASE_CHANCE + logic * CHESS_LOGIC_PER),
        )
        success = random.random() < chance

        # Skill XP regardless of outcome
        try:
            challenger.skills.gain_xp("logic", 2.0)
        except Exception:
            pass

        if success:
            challenger.emotion.add(
                "pride", 0.9, duration=10, source="chess:beat_grim"
            )
            return {
                "ok": True,
                "success": True,
                "chance": round(chance, 2),
                "message": "Checkmate! Grim scowls and reluctantly spares the Sim.",
            }
        else:
            challenger.emotion.add(
                "sadness", 0.6, duration=5, source="chess:lost_to_grim"
            )
            return {
                "ok": True,
                "success": False,
                "chance": round(chance, 2),
                "message": "Grim wins easily. The Sim's fate is sealed.",
            }

    def pet_save_attempt(self, pet: "Sim", dying_sim: "Sim") -> dict:
        """
        A pet tries to harass Grim into sparing their master.
        Requires best-friend relationship. One-time use per pet.
        """
        if not self.is_present:
            return {"ok": False, "reason": "grim_not_present"}
        if pet.sim_id in self._pet_saves_used:
            return {"ok": False, "reason": "pet_already_used_save"}

        # Friendship check
        friendship = 0.0
        try:
            from engine.engine import _current_engine
            if _current_engine:
                rec = _current_engine.relationships.get(pet.sim_id, dying_sim.sim_id)
                friendship = rec.friendship
        except Exception:
            pass

        if friendship < 70:
            return {"ok": False, "reason": "not_close_enough_friends"}

        # Trait modifiers
        pet_traits = set(getattr(pet, "profile", {}).get("traits", []))
        bonus = sum(PET_SAVE_BRAVE_BONUS for t in pet_traits if t in PET_BRAVE_TRAITS)
        malus = sum(PET_SAVE_COWARD_MALUS for t in pet_traits if t in PET_COWARD_TRAITS)
        chance = min(0.90, max(0.05, PET_SAVE_BASE + bonus - malus))
        success = random.random() < chance

        self._pet_saves_used.add(pet.sim_id)

        if success:
            pet.emotion.add("joy", 0.8, duration=8, source="pet_save:success")
            return {
                "ok": True,
                "success": True,
                "chance": round(chance, 2),
                "message": f"{pet.name} harasses Grim until he surrenders. The Sim lives!",
            }
        else:
            pet.emotion.add(
                "sadness", 0.6, duration=6, source="pet_save:failed"
            )
            return {
                "ok": True,
                "success": False,
                "chance": round(chance, 2),
                "message": f"{pet.name} tried bravely but Grim was unmoved.",
            }

    # ── Engine tick ───────────────────────────────────────────────────────────

    def tick(self, engine) -> None:
        """Called each engine tick while Grim may be present."""
        if not self.is_present:
            return

        self._linger_ticks_left -= 1
        if self._linger_ticks_left <= 0:
            self._depart(engine)
            return

        # While lingering: add grim-specific interaction candidates for sims on lot
        if self._linger:
            for sim in engine.sims:
                if sim.household_id == self.lot_id and not sim.is_ghost:
                    self._apply_grim_social_pressure(sim, engine)

    def _apply_grim_social_pressure(self, sim: "Sim", engine) -> None:
        """Push Sims toward grim-specific interactions while he lingers."""
        try:
            from core.moodlets import MoodletStack
            if hasattr(sim, "moodlets"):
                sim.moodlets.add("unsettled", source="grim_reaper_present")
        except Exception:
            pass

    def _depart(self, engine) -> None:
        """Grim leaves the lot."""
        self.is_present = False
        self._linger = False
        self._deaths_this_visit = 0
        self.lot_id = ""
        try:
            engine._bus.emit(
                "grim_departed",
                lot_id=self.lot_id,
                tick=engine._tick_count,
            )
        except Exception:
            pass

    # ── State export ──────────────────────────────────────────────────────────

    def state(self) -> dict:
        return {
            "is_present":         self.is_present,
            "lot_id":             self.lot_id,
            "lingering":          self._linger,
            "linger_ticks_left":  self._linger_ticks_left,
            "deaths_this_visit":  self._deaths_this_visit,
            "tombstones":         list(self.tombstones),
            "personality":        self.personality,
            "available_interactions": GRIM_SOCIAL_INTERACTIONS if self.is_present and self._linger else [],
        }


# ── Personality generation ────────────────────────────────────────────────────

def _generate_grim_personality() -> dict:
    """Generate Grim's fixed personality. Always has at least one mean/evil trait."""
    mean_pool = ["evil", "mean_spirited", "cold", "intimidating"]
    other_pool = ["bookworm", "loves_outdoors", "eccentric", "mysterious", "perceptive"]
    traits = [random.choice(mean_pool)] + random.sample(other_pool, k=2)
    return {
        "name": "Grim Reaper",
        "traits": traits,
        "ocean": {
            "openness":       round(random.uniform(0.3, 0.6), 2),
            "conscientiousness": round(random.uniform(0.7, 0.9), 2),
            "extraversion":   round(random.uniform(0.2, 0.4), 2),
            "agreeableness":  round(random.uniform(0.05, 0.25), 2),
            "neuroticism":    round(random.uniform(0.1, 0.3), 2),
        },
        "social_stance": "cold_to_sims",
        "pet_stance": "kind_to_pets",
        "mbti": "INTJ",
    }
