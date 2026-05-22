from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim


GOAL_NEED_MAP = {
    "hunger": "hunger",
    "eat": "hunger",
    "rest": "energy",
    "sleep": "energy",
    "social": "social",
    "talk": "social",
    "fun": "fun",
    "learn": "fun",
    "study": "fun",
    "clean": "hygiene",
}

NEED_TO_OBJECT_TYPES = {
    "hunger": {"food", "booster", "medical", "temporary", "drug", "alcohol", "misc"},
    "energy": {"booster", "medical"},
    "social": {"misc", "collectible", "jewelry"},
    "fun": {"temporary", "drug", "collectible", "book", "artifact"},
    "hygiene": {"medical", "utility"},
}


class NeuralInteractionPolicy:
    """
    Three-phase learner:
    - Phase 1: contextual interaction/object bandit
    - Phase 2: value head for short-horizon outcome prediction
    - Phase 3: simple planner (acquire object -> use object -> social action)
    """

    def __init__(self) -> None:
        self.lr = 0.04
        self.value_lr = 0.02
        self.reg = 0.0005
        self.conf_threshold = 0.57
        self.interaction_weights: dict[str, list[float]] = {}
        self.value_weights: list[float] = [
            random.uniform(-0.05, 0.05) for _ in range(12)
        ]
        self.affordance_scores: dict[tuple[str, str], float] = {}
        self.stats = {
            "plans_generated": 0,
            "store_runs": 0,
            "uses": 0,
            "social_overrides": 0,
            "observations": 0,
            "avg_reward": 0.0,
            "success_rate": 0.0,
        }
        self._succ = 0
        self.consequence_queue: list[dict] = []
        self.learned_affordances: dict[str, set[str]] = {}

    def extract_features(self, sim, goal_text: str, need_name: str) -> list[float]:
        traits = set(str(t).lower() for t in sim.profile.get("traits", []))
        ks = getattr(sim, "knowledge_aspiration", None)
        cur = float(getattr(ks, "curiosity", 0.0)) if ks else 0.0
        learn = float(getattr(ks, "learning_drive", 0.0)) if ks else 0.0
        return [
            float(sim.needs.hunger) / 100.0,
            float(sim.needs.energy) / 100.0,
            float(sim.needs.social) / 100.0,
            float(sim.needs.fun) / 100.0,
            float(sim.needs.hygiene) / 100.0,
            float(sim.emotion.dominant_valence),
            min(1.0, float(sim.simoleons) / 10000.0),
            min(1.0, sum(sim.skills.levels.values()) / 80.0),
            1.0 if "brave" in traits or "bold" in traits else 0.0,
            1.0 if "coward" in traits else 0.0,
            cur,
            learn,
        ]

    def score_interaction(
        self, sim, action: str, base_weight: float, features: list[float]
    ) -> float:
        key = self._canonical_action(action)
        w = self.interaction_weights.get(key)
        if w is None:
            w = [random.uniform(-0.05, 0.05) for _ in range(len(features))]
            self.interaction_weights[key] = w
        dot = sum(a * b for a, b in zip(features, w))
        return max(0.01, base_weight * (1.0 + math.tanh(dot) * 0.45))

    def choose_goal_need(self, sim) -> tuple[str, str]:
        goal_text = ""
        if getattr(sim, "active_wants", None):
            goal_text = max(
                sim.active_wants, key=lambda w: float(w.priority)
            ).description
        low = goal_text.lower()
        for k, need in GOAL_NEED_MAP.items():
            if k in low:
                return goal_text, need
        pressures = sim.needs.pressure_vector()
        need = max(pressures, key=pressures.get)
        return goal_text or f"satisfy {need}", need

    def plan_for_sim(self, engine, sim) -> dict:
        goal_text, need = self.choose_goal_need(sim)
        feats = self.extract_features(sim, goal_text, need)
        plan = {
            "goal": goal_text,
            "need": need,
            "features": feats,
            "confidence": 0.0,
            "action": None,
            "object_id": None,
            "object_type": None,
            "acquired": False,
            "purchase": None,
        }

        # Phase 3 planner: object affordance + acquisition
        obj = self._best_object_for_need(sim, need)
        if obj is None:
            purchased = self._try_store_acquire(engine, sim, need)
            if purchased is not None:
                plan["acquired"] = True
                plan["purchase"] = purchased
                self.stats["store_runs"] += 1
                obj = self._best_object_for_need(sim, need)
        if obj is not None:
            plan["object_id"] = int(obj.get("id", -1))
            plan["object_type"] = str(obj.get("type", "")).lower()
            plan["confidence"] = 0.62
            plan["action"] = "use_item"
        else:
            choices = [
                "ask thoughtful questions",
                "discuss new theories",
                "debate an idea",
                "share a learning insight",
                "chat",
            ]
            best = "chat"
            best_v = -999.0
            for act in choices:
                v = self._predict_value(feats) + random.uniform(-0.08, 0.08)
                if "debate" in act:
                    v += feats[7] * 0.2
                if "questions" in act:
                    v += feats[10] * 0.2
                if v > best_v:
                    best_v = v
                    best = act
            plan["action"] = "social_override"
            plan["social_action"] = best
            plan["confidence"] = max(0.45, min(0.85, 0.5 + best_v * 0.1))

        self.stats["plans_generated"] += 1
        sim._neural_plan = plan
        return plan

    def _best_object_for_need(self, sim, need: str):
        allowed = NEED_TO_OBJECT_TYPES.get(need, set())
        best = None
        best_score = -999.0
        for item in list(getattr(sim, "inventory_objects", [])):
            typ = str(item.get("type", "")).lower()
            key = (need, typ)
            learned = self.affordance_scores.get(key, 0.0)
            score = (
                learned
                + (0.3 if typ in allowed else -0.1)
                + float(item.get("rarity_score", 0.0))
            )
            if score > best_score:
                best_score = score
                best = item
        return best

    def _try_store_acquire(self, engine, sim, need: str):
        lot_id = "shopping_center"
        stock = engine.objects.lot_object_stock.get(lot_id, {})
        allowed = NEED_TO_OBJECT_TYPES.get(need, set())
        choices = []
        for oid, qty in stock.items():
            if qty <= 0:
                continue
            obj = engine.objects.catalog.get(int(oid))
            if not obj:
                continue
            typ = str(obj.type).lower()
            if typ not in allowed:
                continue
            price = float(engine.objects.current_price(lot_id, int(oid)))
            choices.append((price, int(oid)))
        if not choices:
            for oid, qty in stock.items():
                if qty <= 0:
                    continue
                obj = engine.objects.catalog.get(int(oid))
                if not obj:
                    continue
                price = float(engine.objects.current_price(lot_id, int(oid)))
                choices.append((price, int(oid)))
            if not choices:
                return None
        choices.sort(key=lambda x: x[0])
        price, oid = choices[0]
        if sim.simoleons < price:
            return None
        result = engine.buy_item(sim.sim_id, lot_id, oid, qty=1)
        if not result.get("ok"):
            return None
        return {"lot_id": lot_id, "object_id": oid, "price": round(price, 2)}

    def apply_pre_interaction(self, sim) -> str | None:
        plan = getattr(sim, "_neural_plan", None)
        if not plan:
            return None
        if (
            plan.get("action") == "social_override"
            and plan.get("confidence", 0.0) >= self.conf_threshold
        ):
            self.stats["social_overrides"] += 1
            return str(plan.get("social_action") or "chat")
        return None

    def _predict_value(self, feats: list[float]) -> float:
        return sum(
            self.value_weights[i] * float(feats[i])
            for i in range(len(self.value_weights))
        )

    def observe(self, sim, plan: dict, reward: float, success: bool) -> None:
        if not plan:
            return
        feats = plan.get("features")
        if not feats:
            return
        self.stats["observations"] += 1
        self.stats["avg_reward"] = (self.stats["avg_reward"] * 0.98) + (reward * 0.02)
        if success:
            self._succ += 1
        self.stats["success_rate"] = self._succ / max(1, self.stats["observations"])

        # Phase 1 policy update for chosen social action
        action = plan.get("social_action")
        key = self._canonical_action(str(action)) if action else ""
        if key and key in self.interaction_weights:
            w = self.interaction_weights[key]
            for i in range(len(w)):
                grad = reward * float(feats[i]) - self.reg * w[i]
                w[i] += self.lr * grad
            if reward > 0.45 and action:
                self._queue_consequence(sim, str(key), float(reward))

        # Phase 2 value head update
        pred = self._predict_value(feats)
        err = reward - pred
        for i in range(len(self.value_weights)):
            self.value_weights[i] += self.value_lr * err * float(feats[i])

        # Affordance update
        oid = plan.get("object_id")
        need = str(plan.get("need", ""))
        if oid is not None and need:
            typ = str(plan.get("object_type", "")).lower()
            if typ:
                key = (need, typ)
                cur = self.affordance_scores.get(key, 0.0)
                self.affordance_scores[key] = max(
                    -2.0, min(2.0, cur + (0.07 if success else -0.04) + reward * 0.01)
                )
                if success and reward > 0.25:
                    self.learned_affordances.setdefault(need, set()).add(typ)

    def debug_state(self) -> dict:
        top = sorted(
            (
                (k, sum(abs(x) for x in v) / max(1, len(v)))
                for k, v in self.interaction_weights.items()
            ),
            key=lambda x: x[1],
            reverse=True,
        )[:20]
        return {
            "stats": dict(self.stats),
            "known_actions": len(self.interaction_weights),
            "known_affordances": len(self.affordance_scores),
            "top_action_keys": [k for k, _ in top],
            "queued_consequences": len(self.consequence_queue),
            "discovered_affordances": {
                k: sorted(v) for k, v in self.learned_affordances.items()
            },
        }

    def pop_consequences(self, limit: int = 20) -> list[dict]:
        out = self.consequence_queue[:limit]
        self.consequence_queue = self.consequence_queue[limit:]
        return out

    def _queue_consequence(self, sim, action_key: str, reward: float) -> None:
        followups = {
            "learning": "mentorship_opportunity",
            "support": "trust_debt",
            "rumour": "scandal_ripple",
            "conflict": "rivalry_escalation",
            "romance": "relationship_milestone",
            "story": "memory_bonding",
        }
        if action_key not in followups:
            return
        self.consequence_queue.append(
            {
                "sim_id": sim.sim_id,
                "type": followups[action_key],
                "action_key": action_key,
                "intensity": max(0.08, min(0.7, reward / 2.6)),
            }
        )

    def _canonical_action(self, action: str) -> str:
        low = (action or "").strip().lower()
        if not low:
            return "chat"
        low = re.sub(r"\[[^\]]+\]", " ", low)
        low = low.replace("sim a", "").replace("sim b", "")
        low = re.sub(r"\s+", " ", low).strip()

        buckets = [
            ("condolence", ["condolence", "recent loss", "grief"]),
            ("support", ["support", "comfort", "check in", "share feelings"]),
            ("story", ["story", "memory", "reminisce"]),
            ("romance", ["flirt", "romance", "hold hands", "serenade", "kiss"]),
            ("conflict", ["insult", "argue", "mock", "roast", "fight"]),
            ("humor", ["joke", "impression", "funny", "tease"]),
            ("rumour", ["rumour", "gossip", "spread"]),
            ("learning", ["theory", "debate", "learn", "study", "question"]),
            ("celebration", ["celebrate", "holiday", "party"]),
            ("food", ["cook", "meal", "drink", "snack"]),
        ]
        for bucket, keys in buckets:
            if any(k in low for k in keys):
                return bucket

        toks = [t for t in re.split(r"[^a-z0-9]+", low) if t]
        if not toks:
            return "chat"
        return "_".join(toks[:4])


# ── Compositional Action Programs ────────────────────────────────────────────

class ActionPhase(str, Enum):
    PLAN     = "plan"
    ACQUIRE  = "acquire"
    TRAVEL   = "travel"
    EXECUTE  = "execute"
    RECOVER  = "recover"


@dataclass
class ActionProgram:
    """
    A multi-step action chain: plan → acquire → travel → execute → recover.
    Attached to sim._action_program when the intention system spawns one.

    Each phase maps to an interaction bias in the scheduler. The program
    advances when the current-phase condition is satisfied (or times out).
    """
    sim_id:      str
    goal_type:   str
    phases:      list[ActionPhase] = field(default_factory=lambda: list(ActionPhase))
    phase_idx:   int   = 0
    tick_started: int  = 0
    max_ticks:   int   = 30        # total program lifetime
    ticks_in_phase: int = 0
    max_ticks_per_phase: int = 8
    fallback:    "ActionProgram | None" = None  # used if blocked
    completed:   bool  = False
    interrupted: bool  = False

    @property
    def current_phase(self) -> ActionPhase:
        if self.phase_idx >= len(self.phases):
            return ActionPhase.RECOVER
        return self.phases[self.phase_idx]

    def advance(self) -> bool:
        """Move to the next phase. Returns False when program is complete."""
        self.phase_idx += 1
        self.ticks_in_phase = 0
        if self.phase_idx >= len(self.phases):
            self.completed = True
            return False
        return True

    def interaction_bias(self) -> str:
        """Map current phase → scheduler interaction bias."""
        _MAP = {
            ActionPhase.PLAN:    "intellectual",
            ActionPhase.ACQUIRE: "activity",
            ActionPhase.TRAVEL:  "activity",
            ActionPhase.EXECUTE: "activity",
            ActionPhase.RECOVER: "support",
        }
        return _MAP.get(self.current_phase, "friendly")

    def tick(self) -> None:
        self.ticks_in_phase += 1


class InterruptionHandler:
    """
    Detects when an ActionProgram is blocked (phase timeout, resource
    unavailable, partner refusal) and applies a fallback strategy.
    """

    def handle(
        self,
        program: ActionProgram,
        sim: "Sim",
        blocked_reason: str = "",
    ) -> ActionProgram | None:
        """
        Called when the current phase times out or is explicitly blocked.
        Returns a replacement program or None (abandon the goal).
        """
        if program.fallback:
            program.interrupted = True
            return program.fallback

        # Phase-specific fallback logic
        phase = program.current_phase
        if phase == ActionPhase.ACQUIRE:
            # Can't acquire resource → skip to EXECUTE with what we have
            program.advance()
            return program
        elif phase == ActionPhase.TRAVEL:
            # Can't travel → stay and try EXECUTE locally
            program.advance()
            return program
        elif phase == ActionPhase.EXECUTE:
            # Execution failed → go straight to RECOVER
            program.phase_idx = len(program.phases) - 1
            return program

        # Default: abandon
        program.interrupted = True
        program.completed   = True
        return None

    @staticmethod
    def tick_program(program: ActionProgram, sim: "Sim") -> str:
        """
        Advance the program one tick. Returns the current interaction bias.
        If the phase times out, triggers interruption handling.
        """
        if program.completed or program.interrupted:
            return "friendly"

        program.tick()
        bias = program.interaction_bias()

        if program.ticks_in_phase >= program.max_ticks_per_phase:
            handler = InterruptionHandler()
            result = handler.handle(program, sim, "timeout")
            if result is None:
                sim._action_program = None
                return "friendly"
            sim._action_program = result

        return bias
