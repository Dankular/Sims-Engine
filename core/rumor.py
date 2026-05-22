"""
core/rumor.py — Asymmetric information: rumor propagation, mistaken identity,
hidden relationships, and secret leakage mechanics.

Key properties:
  • Each hop reduces confidence by HOP_DECAY (default 0.15).
  • Rumors can contain mistaken identity — wrong sim_id with low confidence.
  • Hidden relationships exist in ground truth but not in anyone's belief graph
    until a leak event fires.
  • High-valence events (affair, bankruptcy, arrest) have per-tick leak probability.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from core.beliefs import BeliefGraph, BeliefSource

logger = logging.getLogger(__name__)

HOP_DECAY      = 0.15   # confidence loss per propagation hop
MIN_CONFIDENCE = 0.05   # below this, rumor is discarded


@dataclass
class Rumor:
    """A piece of information propagating through the social network."""
    subject_id:  str            # who the rumor is about
    predicate:   str            # what is claimed
    object_:     str            # value or target
    truth:       bool           # whether it's actually true (hidden)
    origin_id:   str            # sim_id who started it
    confidence:  float = 0.8   # starts high, decays per hop
    hop_count:   int   = 0
    spread_to:   set  = field(default_factory=set)  # sim_ids who heard it
    # Mistaken identity: 10% chance the subject_id is wrong
    mistaken:    bool = False
    true_subject_id: str = ""   # the real subject if mistaken

    def propagate(self) -> "Rumor":
        """Return a degraded copy for the next hop."""
        new_conf = max(MIN_CONFIDENCE, self.confidence - HOP_DECAY)
        child = Rumor(
            subject_id=self.subject_id,
            predicate=self.predicate,
            object_=self.object_,
            truth=self.truth,
            origin_id=self.origin_id,
            confidence=new_conf,
            hop_count=self.hop_count + 1,
            spread_to=set(self.spread_to),
            mistaken=self.mistaken,
            true_subject_id=self.true_subject_id,
        )
        return child


@dataclass
class HiddenRelationship:
    """A relationship that exists in ground truth but is invisible to belief graphs."""
    sim_a_id:   str
    sim_b_id:   str
    rel_type:   str             # "affair", "secret_sibling", "double_agent", …
    valence:    float           # importance — higher → higher leak probability
    created_tick: int = 0
    leaked:     bool = False
    leak_probability: float = 0.02  # per tick


class RumorNetwork:
    """
    Global rumor propagation engine.

    Each tick: active rumors spread to 1–2 new sims via social connections.
    Mistaken-identity rumors occasionally name the wrong sim.
    Hidden relationships leak based on valence × random pressure.
    """

    def __init__(self) -> None:
        self._rumors:   list[Rumor]              = []
        self._hidden:   list[HiddenRelationship] = []
        self._listeners: list = []    # callables(rumor, recipient_sim)

    # ── Rumor creation ────────────────────────────────────────────────────────

    def seed_rumor(
        self,
        subject_id: str, predicate: str, object_: str,
        origin_id: str, truth: bool,
        confidence: float = 0.85,
        sims: list["Sim"] | None = None,
    ) -> Rumor:
        """Create a new rumor. With 10% chance, introduce mistaken identity."""
        mistaken = random.random() < 0.10
        true_subj = subject_id
        if mistaken and sims:
            candidates = [s for s in sims if s.sim_id != subject_id]
            if candidates:
                subject_id = random.choice(candidates).sim_id

        r = Rumor(
            subject_id=subject_id, predicate=predicate, object_=object_,
            truth=truth, origin_id=origin_id, confidence=confidence,
            mistaken=mistaken, true_subject_id=true_subj,
        )
        r.spread_to.add(origin_id)
        self._rumors.append(r)
        return r

    def add_hidden_relationship(self, rel: HiddenRelationship) -> None:
        self._hidden.append(rel)

    # ── Propagation tick ──────────────────────────────────────────────────────

    def tick(self, sims: list["Sim"], current_tick: int) -> list[dict]:
        """
        Spread rumors and leak hidden relationships.
        Returns list of events {type, rumor/relationship, recipient_sim}.
        """
        events: list[dict] = []

        if not sims:
            return events

        # Spread each active rumor to 1 new sim
        for rumor in list(self._rumors):
            if rumor.confidence < MIN_CONFIDENCE:
                continue
            candidates = [s for s in sims if s.sim_id not in rumor.spread_to]
            if not candidates:
                continue
            # Prefer sims who know the origin or subject
            recipient = random.choice(candidates)
            child = rumor.propagate()
            child.spread_to.add(recipient.sim_id)
            rumor.spread_to.add(recipient.sim_id)

            # Write into recipient's belief graph
            if hasattr(recipient, "beliefs"):
                from core.beliefs import BeliefSource
                recipient.beliefs.observe(
                    subject=child.subject_id,
                    predicate=child.predicate,
                    object_=child.object_,
                    confidence=child.confidence,
                    source=BeliefSource.RUMOR,
                    tick=current_tick,
                )

            events.append({
                "type":     "rumor_spread",
                "rumor":    child.predicate,
                "about":    child.subject_id,
                "to":       recipient.sim_id,
                "conf":     round(child.confidence, 2),
                "mistaken": child.mistaken,
            })

            for fn in self._listeners:
                try:
                    fn(child, recipient)
                except Exception:
                    pass

        # Leak hidden relationships
        for hr in self._hidden:
            if hr.leaked:
                continue
            # Pressure rises with valence
            p = hr.leak_probability * (1 + abs(hr.valence))
            if random.random() < p:
                hr.leaked = True
                recipients = random.sample(sims, min(2, len(sims)))
                for sim in recipients:
                    if hasattr(sim, "beliefs"):
                        from core.beliefs import BeliefSource
                        sim.beliefs.observe(
                            subject=hr.sim_a_id,
                            predicate=hr.rel_type,
                            object_=hr.sim_b_id,
                            confidence=0.7,
                            source=BeliefSource.RUMOR,
                            tick=current_tick,
                        )
                events.append({
                    "type":     "secret_leaked",
                    "sim_a":    hr.sim_a_id,
                    "sim_b":    hr.sim_b_id,
                    "rel_type": hr.rel_type,
                })

        # Prune dead rumors
        self._rumors = [r for r in self._rumors if r.confidence >= MIN_CONFIDENCE
                        and len(r.spread_to) < len(sims)]

        return events

    def on_rumor_spread(self, fn) -> None:
        self._listeners.append(fn)

    def active_rumors(self) -> list[dict]:
        return [
            {
                "predicate":  r.predicate,
                "about":      r.subject_id,
                "confidence": round(r.confidence, 2),
                "hops":       r.hop_count,
                "spread_to":  len(r.spread_to),
                "mistaken":   r.mistaken,
            }
            for r in self._rumors if r.confidence >= MIN_CONFIDENCE
        ]
