"""
core/beliefs.py — Per-sim causal belief graph + asymmetric information.

Each sim holds a private set of BeliefNodes — what they believe to be true
about the world, other sims, and causal relationships. Beliefs have:
  • Confidence (0..1) — decays with staleness, boosted by confirmation
  • Source — direct observation, rumor, inference
  • Causal links — "if I do X to Y, I expect outcome Z with confidence C"

Engine integration:
  sim.beliefs = BeliefGraph()
  engine._process_beliefs() decays staleness each tick
  engine._apply_resolved() writes new observations into the observer's graph
  llm/context.py injects sim A's beliefs about sim B (not ground truth)
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim

logger = logging.getLogger(__name__)


class BeliefSource(str, Enum):
    OBSERVATION = "observation"   # sim witnessed directly
    RUMOR       = "rumor"         # told by another sim
    INFERENCE   = "inference"     # deduced from pattern
    SELF        = "self"          # about own state


@dataclass
class BeliefNode:
    """A single belief: subject [predicate] object_, with confidence."""
    subject:    str               # sim_id or concept
    predicate:  str               # "is_friend_of", "earns", "is_having_affair_with" …
    object_:    str               # sim_id, value, or concept
    confidence: float             # 0..1
    source:     BeliefSource = BeliefSource.OBSERVATION
    created_tick: int = 0
    updated_tick: int = 0
    staleness_rate: float = 0.002 # confidence decay per tick

    def decay(self, ticks_since_update: int) -> None:
        self.confidence = max(0.0,
            self.confidence - self.staleness_rate * ticks_since_update)

    def reinforce(self, boost: float = 0.1) -> None:
        self.confidence = min(1.0, self.confidence + boost)

    def key(self) -> tuple[str, str, str]:
        return (self.subject, self.predicate, self.object_)


@dataclass
class CausalBelief:
    """
    Causal model entry: "if I do [action] to [target], I expect [outcome]".
    Used by the scheduler to inform risk-weighted interaction choice.
    """
    action:     str
    target_id:  str
    outcome:    str               # e.g. "lose_club_status", "gain_romance"
    valence:    float             # expected valence of outcome
    confidence: float             # 0..1
    evidence_count: int = 1


class BeliefGraph:
    """
    Per-sim private knowledge base.

    Stores factual beliefs (what is true about the world according to this sim)
    and causal beliefs (what this sim expects will happen given an action).

    Crucially this is ASYMMETRIC: different sims have different (possibly wrong)
    beliefs about the same facts. Adjudication uses the observer's beliefs, not
    ground truth — this is the source of misunderstanding, drama, and betrayal.
    """

    DECAY_INTERVAL = 5   # ticks between staleness decays

    def __init__(self) -> None:
        self._facts:   dict[tuple, BeliefNode]  = {}
        self._causal:  dict[tuple, CausalBelief] = {}
        self._last_decay_tick: int = 0

    # ── Factual beliefs ───────────────────────────────────────────────────────

    def observe(
        self,
        subject: str, predicate: str, object_: str,
        confidence: float = 0.9,
        source: BeliefSource = BeliefSource.OBSERVATION,
        tick: int = 0,
    ) -> BeliefNode:
        key = (subject, predicate, object_)
        if key in self._facts:
            node = self._facts[key]
            node.reinforce((confidence - node.confidence) * 0.4 + 0.05)
            node.updated_tick = tick
            node.source = source
        else:
            node = BeliefNode(
                subject=subject, predicate=predicate, object_=object_,
                confidence=confidence, source=source,
                created_tick=tick, updated_tick=tick,
            )
            self._facts[key] = node
        return node

    def get(self, subject: str, predicate: str, object_: str) -> BeliefNode | None:
        return self._facts.get((subject, predicate, object_))

    def beliefs_about(self, subject: str) -> list[BeliefNode]:
        return [n for k, n in self._facts.items() if k[0] == subject and n.confidence > 0.1]

    def confident_beliefs(self, threshold: float = 0.5) -> list[BeliefNode]:
        return [n for n in self._facts.values() if n.confidence >= threshold]

    # ── Causal beliefs ────────────────────────────────────────────────────────

    def update_causal(
        self,
        action: str, target_id: str, outcome: str,
        valence: float, confidence: float,
    ) -> None:
        key = (action, target_id, outcome)
        if key in self._causal:
            cb = self._causal[key]
            cb.evidence_count += 1
            lr = 1 / (cb.evidence_count + 1)
            cb.valence     = cb.valence * (1 - lr) + valence * lr
            cb.confidence  = min(1.0, cb.confidence + 0.05)
        else:
            self._causal[key] = CausalBelief(
                action=action, target_id=target_id, outcome=outcome,
                valence=valence, confidence=confidence,
            )

    def expected_valence(self, action: str, target_id: str) -> float | None:
        """Return confidence-weighted expected valence for an action, or None."""
        relevant = [
            cb for (a, t, _), cb in self._causal.items()
            if a == action and t == target_id and cb.confidence > 0.3
        ]
        if not relevant:
            return None
        total_conf = sum(cb.confidence for cb in relevant)
        return sum(cb.valence * cb.confidence for cb in relevant) / total_conf

    # ── Staleness decay ───────────────────────────────────────────────────────

    def decay_tick(self, current_tick: int) -> None:
        if current_tick - self._last_decay_tick < self.DECAY_INTERVAL:
            return
        gap = current_tick - self._last_decay_tick
        for node in self._facts.values():
            if node.source != BeliefSource.SELF:
                node.decay(gap)
        # Prune zero-confidence beliefs
        self._facts = {k: v for k, v in self._facts.items() if v.confidence > 0.02}
        self._last_decay_tick = current_tick

    # ── Context export ────────────────────────────────────────────────────────

    def context_summary(self, about_sim_id: str, limit: int = 5) -> str:
        """
        Return a compact text snippet for injection into adjudicator context.
        Only includes high-confidence beliefs about `about_sim_id`.
        """
        nodes = [
            n for n in self._facts.values()
            if n.subject == about_sim_id and n.confidence > 0.45
        ]
        nodes.sort(key=lambda n: -n.confidence)
        lines = [
            f"{n.predicate.replace('_',' ')} {n.object_} "
            f"(conf={n.confidence:.0%}, via {n.source})"
            for n in nodes[:limit]
        ]
        return "; ".join(lines) if lines else ""

    def summary_dict(self) -> dict:
        return {
            "total_beliefs":    len(self._facts),
            "causal_models":    len(self._causal),
            "high_confidence":  sum(1 for n in self._facts.values() if n.confidence > 0.7),
        }
