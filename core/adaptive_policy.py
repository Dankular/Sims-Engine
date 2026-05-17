from __future__ import annotations

import json
import math
import random
from pathlib import Path


class AdaptiveBandit:
    """Lightweight per-sim contextual bandit.

    Phase 1 implementation uses Thompson sampling over per-(sim, action)
    success/failure counts, with small context-sensitive shaping.
    """

    def __init__(
        self, store_path: str = "datasets/.sim_cache/adaptive_bandit.json"
    ) -> None:
        self.store_path = Path(store_path)
        self.enabled: bool = True
        self.alpha_blend: float = 0.15
        self._stats: dict[str, dict[str, dict[str, float]]] = {}
        self._load()

    def _load(self) -> None:
        if not self.store_path.exists():
            return
        try:
            payload = json.loads(self.store_path.read_text(encoding="utf-8"))
            self._stats = payload.get("stats", {}) if isinstance(payload, dict) else {}
            self.alpha_blend = float(payload.get("alpha_blend", self.alpha_blend))
        except Exception:
            self._stats = {}

    def save(self) -> None:
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"stats": self._stats, "alpha_blend": self.alpha_blend}
            self.store_path.write_text(json.dumps(payload), encoding="utf-8")
        except Exception:
            pass

    def _bucket(self, sim_id: str, action: str) -> dict[str, float]:
        per_sim = self._stats.setdefault(sim_id, {})
        return per_sim.setdefault(
            action, {"success": 1.0, "fail": 1.0, "n": 0.0, "reward_sum": 0.0}
        )

    def score(self, sim_a, sim_b, action: str, heuristic_weight: float) -> float:
        if not self.enabled:
            return heuristic_weight
        b = self._bucket(sim_a.sim_id, action)
        sampled = random.betavariate(max(1e-3, b["success"]), max(1e-3, b["fail"]))

        # Context shaping from relationship and current mood.
        rel_bias = 0.5
        try:
            rel_bias += max(
                -0.2, min(0.2, (getattr(sim_b, "reputation_score", 0.0) / 100.0) * 0.2)
            )
            rel_bias += max(
                -0.15, min(0.15, (sim_a.emotion.dominant_valence - 0.5) * 0.3)
            )
            rel_bias = max(0.1, min(0.9, rel_bias))
        except Exception:
            pass
        learned_factor = 0.7 + (sampled * rel_bias)  # ~0.7..1.6

        # Confidence grows with observations.
        n = b["n"]
        conf = 1.0 - math.exp(-n / 25.0)
        blend = self.alpha_blend * conf
        return max(0.01, heuristic_weight * ((1.0 - blend) + blend * learned_factor))

    def observe(self, sim_id: str, action: str, reward: float) -> None:
        b = self._bucket(sim_id, action)
        b["n"] += 1.0
        b["reward_sum"] += float(reward)
        # Binary success update + soft magnitude update.
        if reward >= 0:
            b["success"] += 1.0 + min(0.5, reward * 0.05)
        else:
            b["fail"] += 1.0 + min(0.5, abs(reward) * 0.05)

    def debug_for(self, sim_id: str, limit: int = 8) -> list[dict]:
        per = self._stats.get(sim_id, {})
        rows = []
        for action, s in per.items():
            n = float(s.get("n", 0.0))
            avg = float(s.get("reward_sum", 0.0)) / n if n > 0 else 0.0
            rows.append(
                {
                    "action": action,
                    "n": int(n),
                    "success": round(float(s.get("success", 0.0)), 3),
                    "fail": round(float(s.get("fail", 0.0)), 3),
                    "avg_reward": round(avg, 3),
                }
            )
        rows.sort(key=lambda x: x["n"], reverse=True)
        return rows[:limit]
