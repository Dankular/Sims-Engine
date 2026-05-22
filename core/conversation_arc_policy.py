"""
core/conversation_arc_policy.py — Personality-aware online learner for
conversation arc stage weights.

Architecture
------------
Two-layer linear contextual model:

  Global layer  (13 → 1 per stage)  — personality-cluster prior shared across
                                       all sims; converges quickly from the first
                                       few dozen interactions.

  Per-sim delta (13 → 1 per stage)  — fine-tunes each individual sim's arc on
                                       top of the global prior; starts at zero
                                       and grows as the sim accumulates history.

Features (13 dims)
------------------
  0-4   sim_a OCEAN (openness, conscientiousness, extraversion, agreeableness, neuroticism)
  5-9   sim_b OCEAN
  10    romance  (normalised 0–1)
  11    friendship (normalised 0–1)
  12    sim_a has a flirty/alluring moodlet (0 or 1)

Output
------
  stage_multiplier(sim_a, sim_b, rel, stage) → float in [0.3, 2.5]
  Applied to:
    - interaction weight boosts in _apply_stage_weights
    - dwell-time thresholds in _advance_conversation_stage

Learning
--------
  Logistic loss, SGD.  Called once per resolved interaction.
  Reward = composite valence + relationship-delta signal from _apply_resolved.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.sim import Sim
    from core.relationships import RelationshipRecord

N_FEATURES = 13
STAGES = ("small_talk", "teasing", "disclosure", "affectionate_intent")

_DEFAULT_STORE = "datasets/.sim_cache/arc_policy.json"


def _dot(w: list[float], feats: list[float]) -> float:
    return sum(a * b for a, b in zip(w, feats))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, x))))


class ConversationArcPolicy:
    """Personality-aware stage-weight learner — see module docstring."""

    def __init__(
        self,
        lr: float = 0.04,
        per_sim_lr: float = 0.015,
        reg: float = 0.0005,
        store_path: str = _DEFAULT_STORE,
    ) -> None:
        self.lr = lr
        self.per_sim_lr = per_sim_lr
        self.reg = reg
        self.store_path = Path(store_path)

        # Global personality-cluster weights
        self._global_w: dict[str, list[float]] = {
            s: [0.0] * N_FEATURES for s in STAGES
        }
        self._global_b: dict[str, float] = {s: 0.0 for s in STAGES}

        # Per-sim fine-tuning delta (starts at zero)
        self._sim_w: dict[str, dict[str, list[float]]] = {}

        self._n_obs: int = 0
        self._load()

    # ── Feature extraction ────────────────────────────────────────────────────

    def extract_features(self, sim_a: Any, sim_b: Any, rel: Any) -> list[float]:
        oa = sim_a.profile.get("ocean", {})
        ob = sim_b.profile.get("ocean", {})
        is_flirty = 0.0
        try:
            moodlets = getattr(sim_a, "moodlets", None)
            if moodlets is not None:
                is_flirty = 1.0 if any(
                    moodlets.has(k)
                    for k in ("flirty", "alluring", "in_the_mood", "love_is_in_the_air")
                ) else 0.0
        except Exception:
            pass
        return [
            float(oa.get("openness",          0.5)),
            float(oa.get("conscientiousness",  0.5)),
            float(oa.get("extraversion",       0.5)),
            float(oa.get("agreeableness",      0.5)),
            float(oa.get("neuroticism",        0.5)),
            float(ob.get("openness",          0.5)),
            float(ob.get("conscientiousness",  0.5)),
            float(ob.get("extraversion",       0.5)),
            float(ob.get("agreeableness",      0.5)),
            float(ob.get("neuroticism",        0.5)),
            min(1.0, float(getattr(rel, "romance",    0.0)) / 100.0),
            min(1.0, float(getattr(rel, "friendship", 0.0)) / 100.0),
            is_flirty,
        ]

    # ── Inference ─────────────────────────────────────────────────────────────

    def stage_multiplier(
        self,
        sim_a: Any,
        sim_b: Any,
        rel: Any,
        stage: str,
    ) -> float:
        """Return a multiplier in [0.3, 2.5] for stage weights.

        > 1.0  → lean into this stage more aggressively / advance faster
        < 1.0  → lighter touch / advance more cautiously
        """
        if stage not in STAGES:
            return 1.0
        feats = self.extract_features(sim_a, sim_b, rel)
        dot = (
            _dot(self._global_w[stage], feats)
            + self._global_b[stage]
            + _dot(
                self._sim_w.get(sim_a.sim_id, {}).get(stage, [0.0] * N_FEATURES),
                feats,
            )
        )
        return 0.3 + _sigmoid(dot) * 2.2  # maps [-∞,∞] → [0.3, 2.5]

    # ── Online learning ───────────────────────────────────────────────────────

    def observe(
        self,
        sim_a: Any,
        sim_b: Any,
        rel: Any,
        stage: str,
        valence: float,
        reward: float,
    ) -> None:
        """SGD update from a resolved interaction.

        reward  — composite signal from _apply_resolved (fd*0.6 + rd*0.4 + valence*6 + …)
        valence — adjudicator valence [-1, 1]
        """
        if stage not in STAGES:
            return
        feats = self.extract_features(sim_a, sim_b, rel)

        # Current prediction
        sw = self._sim_w.get(sim_a.sim_id, {}).get(stage, [0.0] * N_FEATURES)
        dot = (
            _dot(self._global_w[stage], feats)
            + self._global_b[stage]
            + _dot(sw, feats)
        )
        sig = _sigmoid(dot)

        # Target: map reward → logistic target in [0, 1]
        # reward typically lives in [-6, +12]; centre at 0.5
        target = max(0.0, min(1.0, 0.5 + reward / 20.0))
        err = sig - target  # logistic gradient (scalar)

        # ── Global layer update ──────────────────────────────────────────────
        gw = self._global_w[stage]
        for i in range(N_FEATURES):
            gw[i] -= self.lr * (err * feats[i] + self.reg * gw[i])
        self._global_b[stage] -= self.lr * err

        # ── Per-sim delta update ─────────────────────────────────────────────
        if sim_a.sim_id not in self._sim_w:
            self._sim_w[sim_a.sim_id] = {}
        if stage not in self._sim_w[sim_a.sim_id]:
            self._sim_w[sim_a.sim_id][stage] = [0.0] * N_FEATURES
        sw_mut = self._sim_w[sim_a.sim_id][stage]
        for i in range(N_FEATURES):
            sw_mut[i] -= self.per_sim_lr * (err * feats[i] + self.reg * sw_mut[i])

        self._n_obs += 1

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self) -> None:
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "n_obs": self._n_obs,
                "lr": self.lr,
                "per_sim_lr": self.per_sim_lr,
                "reg": self.reg,
                "global_w": self._global_w,
                "global_b": self._global_b,
                "sim_w": {
                    sid: {stage: list(v) for stage, v in stages.items()}
                    for sid, stages in self._sim_w.items()
                },
            }
            self.store_path.write_text(json.dumps(payload), encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> None:
        if not self.store_path.exists():
            return
        try:
            p = json.loads(self.store_path.read_text(encoding="utf-8"))
            self._n_obs = int(p.get("n_obs", 0))
            for s in STAGES:
                gw = p.get("global_w", {}).get(s)
                if isinstance(gw, list) and len(gw) == N_FEATURES:
                    self._global_w[s] = [float(x) for x in gw]
                gb = p.get("global_b", {}).get(s)
                if gb is not None:
                    self._global_b[s] = float(gb)
            for sid, stages in p.get("sim_w", {}).items():
                self._sim_w[sid] = {}
                for stage, v in stages.items():
                    if isinstance(v, list) and len(v) == N_FEATURES:
                        self._sim_w[sid][stage] = [float(x) for x in v]
        except Exception:
            pass

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def debug_global(self) -> dict:
        """Top-feature importances per stage (|weight| magnitude)."""
        _FEAT_NAMES = [
            "a_open", "a_consc", "a_extra", "a_agree", "a_neur",
            "b_open", "b_consc", "b_extra", "b_agree", "b_neur",
            "romance", "friendship", "flirty",
        ]
        out: dict = {"n_obs": self._n_obs, "stages": {}}
        for s in STAGES:
            ranked = sorted(
                zip(_FEAT_NAMES, self._global_w[s]),
                key=lambda x: abs(x[1]),
                reverse=True,
            )
            out["stages"][s] = {
                "bias": round(self._global_b[s], 4),
                "top_features": [
                    {"feat": n, "w": round(w, 4)} for n, w in ranked[:5]
                ],
            }
        return out

    def debug_sim(self, sim_id: str) -> dict:
        """Per-sim fine-tuning delta norms per stage."""
        sim_stages = self._sim_w.get(sim_id, {})
        return {
            s: {
                "l2_norm": round(
                    math.sqrt(sum(x * x for x in sim_stages.get(s, []))), 4
                ),
                "n_stages_learned": len(sim_stages),
            }
            for s in STAGES
        }
