"""
analytics/emergence.py — Emergence calibration dashboard.

Tracks whether simulation behavior is genuinely emergent vs noisy by
computing six measurable dynamics every SNAPSHOT_INTERVAL ticks:

  policy_diversity     — Shannon entropy of interaction type distribution
  path_dependence      — autocorrelation of sim wealth rank over time
  inequality           — Gini coefficient of simoleons distribution
  social_mobility      — Spearman rank correlation change per 50 ticks
  conflict_half_life   — median ticks from hostile→neutral relationship
  reconciliation_rate  — fraction of hostile pairs reaching neutral in window
  novelty_score        — fraction of event types unseen in prior 50 ticks

Exposed via GET /analytics/emergence on the server.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL = 10     # compute metrics every N ticks
HISTORY_WINDOW    = 50     # ticks to look back for mobility / novelty


@dataclass
class EmergenceSnapshot:
    tick:               int
    policy_diversity:   float
    path_dependence:    float
    inequality:         float
    social_mobility:    float
    conflict_half_life: float
    reconciliation_rate: float
    novelty_score:      float

    def to_dict(self) -> dict:
        return {
            "tick":               self.tick,
            "policy_diversity":   round(self.policy_diversity, 4),
            "path_dependence":    round(self.path_dependence, 4),
            "inequality":         round(self.inequality, 4),
            "social_mobility":    round(self.social_mobility, 4),
            "conflict_half_life": round(self.conflict_half_life, 2),
            "reconciliation_rate": round(self.reconciliation_rate, 4),
            "novelty_score":      round(self.novelty_score, 4),
        }


class EmergenceDashboard:

    def __init__(self) -> None:
        self._snapshots:  list[EmergenceSnapshot]          = []
        self._interaction_counts: dict[str, int]           = defaultdict(int)
        self._wealth_ranks: deque[dict[str, int]]          = deque(maxlen=HISTORY_WINDOW)
        self._event_types_seen: deque[set[str]]            = deque(maxlen=HISTORY_WINDOW)
        self._hostile_pair_onset: dict[tuple, int]         = {}  # pair → tick went hostile
        self._reconciled_pairs:   list[tuple[tuple, int]]  = []  # (pair, ticks_to_reconcile)

    # ── Main tick ─────────────────────────────────────────────────────────────

    def snapshot(self, engine: "SimEngine") -> EmergenceSnapshot | None:
        if engine.tick_count % SNAPSHOT_INTERVAL != 0:
            return None

        sims = engine.sims
        if not sims:
            return None

        # Collect interaction type counts for this window
        self._update_from_pending(engine)
        self._update_wealth_rank(sims, engine.tick_count)
        self._update_hostile_tracking(engine)

        snap = EmergenceSnapshot(
            tick=engine.tick_count,
            policy_diversity=self._policy_diversity(),
            path_dependence=self._path_dependence(),
            inequality=self._gini(sims),
            social_mobility=self._social_mobility(),
            conflict_half_life=self._conflict_half_life(),
            reconciliation_rate=self._reconciliation_rate(engine),
            novelty_score=self._novelty_score(engine),
        )
        self._snapshots.append(snap)
        self._snapshots = self._snapshots[-500:]

        logger.debug(
            "[Emergence] t=%d | diversity=%.3f | gini=%.3f | mobility=%.3f | novelty=%.3f",
            snap.tick, snap.policy_diversity, snap.inequality,
            snap.social_mobility, snap.novelty_score,
        )
        return snap

    # ── Metric computation ────────────────────────────────────────────────────

    def _policy_diversity(self) -> float:
        """Shannon entropy of interaction type distribution. Max = log2(N_types)."""
        total = sum(self._interaction_counts.values())
        if total == 0:
            return 0.0
        entropy = 0.0
        for count in self._interaction_counts.values():
            p = count / total
            if p > 0:
                entropy -= p * math.log2(p)
        n = len(self._interaction_counts)
        max_entropy = math.log2(n) if n > 1 else 1.0
        return entropy / max_entropy if max_entropy > 0 else 0.0

    def _path_dependence(self) -> float:
        """
        Autocorrelation of wealth rank at lag=HISTORY_WINDOW//2.
        High = ranks are stable (stratified); low = random / volatile.
        """
        if len(self._wealth_ranks) < 4:
            return 0.0
        ranks_old = self._wealth_ranks[0]
        ranks_new = self._wealth_ranks[-1]
        common = set(ranks_old) & set(ranks_new)
        if len(common) < 2:
            return 0.0
        xs = [ranks_old[sid] for sid in common]
        ys = [ranks_new[sid] for sid in common]
        return _spearman(xs, ys)

    def _gini(self, sims) -> float:
        """Gini coefficient of simoleons. 0 = perfect equality, 1 = maximum inequality."""
        values = sorted(max(0.0, s.simoleons) for s in sims)
        n = len(values)
        if n == 0 or sum(values) == 0:
            return 0.0
        cumsum = 0.0
        gini_num = 0.0
        for i, v in enumerate(values):
            cumsum += v
            gini_num += (2 * (i + 1) - n - 1) * v
        return gini_num / (n * sum(values))

    def _social_mobility(self) -> float:
        """
        1 - |rank_correlation(now, HISTORY_WINDOW ago)|.
        High = lots of mobility; low = frozen hierarchy.
        """
        return 1.0 - abs(self._path_dependence())

    def _conflict_half_life(self) -> float:
        """Median ticks from hostile onset to reconciliation."""
        if not self._reconciled_pairs:
            return float("inf")
        durations = sorted(t for _, t in self._reconciled_pairs)
        mid = len(durations) // 2
        return float(durations[mid])

    def _reconciliation_rate(self, engine: "SimEngine") -> float:
        """Fraction of hostile pairs that reconciled in the last HISTORY_WINDOW ticks."""
        window_start = engine.tick_count - HISTORY_WINDOW
        recent = sum(
            1 for _, t in self._reconciled_pairs
            if t > 0
        )
        total_hostile = len(self._hostile_pair_onset) + len(self._reconciled_pairs)
        if total_hostile == 0:
            return 0.0
        return recent / total_hostile

    def _novelty_score(self, engine: "SimEngine") -> float:
        """Fraction of event types in the last tick not seen in the prior window."""
        if len(self._event_types_seen) < 2:
            return 0.0
        current  = self._event_types_seen[-1]
        history  = set().union(*list(self._event_types_seen)[:-1])
        if not current:
            return 0.0
        novel = current - history
        return len(novel) / len(current)

    # ── Data collectors ───────────────────────────────────────────────────────

    def record_interaction(self, interaction_type: str) -> None:
        self._interaction_counts[interaction_type] += 1

    def _update_from_pending(self, engine: "SimEngine") -> None:
        # Collect from pending interactions completed this tick
        current_events: set[str] = set()
        for item in getattr(engine, "_pending", []):
            self._interaction_counts[item.interaction] += 1
            current_events.add(item.interaction)
        self._event_types_seen.append(current_events)

    def _update_wealth_rank(self, sims, tick: int) -> None:
        ordered = sorted(sims, key=lambda s: -s.simoleons)
        ranks = {s.sim_id: i for i, s in enumerate(ordered)}
        self._wealth_ranks.append(ranks)

    def _update_hostile_tracking(self, engine: "SimEngine") -> None:
        tick = engine.tick_count
        try:
            for (a, b), rec in engine.relationships.all_pairs():
                pair = (min(a, b), max(a, b))
                label = rec.state_label()
                if label in ("enemy", "hostile", "rivals"):
                    if pair not in self._hostile_pair_onset:
                        self._hostile_pair_onset[pair] = tick
                else:
                    if pair in self._hostile_pair_onset:
                        onset = self._hostile_pair_onset.pop(pair)
                        self._reconciled_pairs.append((pair, tick - onset))
                        self._reconciled_pairs = self._reconciled_pairs[-200:]
        except Exception:
            pass

    # ── API ───────────────────────────────────────────────────────────────────

    def latest(self) -> dict | None:
        return self._snapshots[-1].to_dict() if self._snapshots else None

    def history(self, n: int = 50) -> list[dict]:
        return [s.to_dict() for s in self._snapshots[-n:]]

    def trend(self, metric: str, n: int = 20) -> list[float]:
        return [
            getattr(s, metric, 0.0)
            for s in self._snapshots[-n:]
        ]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _spearman(xs: list, ys: list) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    rx = _rank(xs)
    ry = _rank(ys)
    d2 = sum((a - b) ** 2 for a, b in zip(rx, ry))
    return 1 - 6 * d2 / (n * (n * n - 1))


def _rank(values: list) -> list[float]:
    sorted_vals = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * len(values)
    for rank, (idx, _) in enumerate(sorted_vals):
        ranks[idx] = float(rank + 1)
    return ranks
