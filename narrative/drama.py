"""
narrative/drama.py — Drama cascade: witnesses, sides, enemy-of-my-friend.

When a negative interaction resolves (valence < DRAMA_VALENCE_THRESHOLD),
other sims at the same venue who have relationships with both parties
are affected:

  1. Witnesses observe the event → gain gossip with credibility bonus
  2. Sides: witnesses who are friends with BOTH parties face social pressure
     - Their relationship with the "aggressor" (sim_a in mean interactions) decays
     - Their relationship with the "victim" (sim_b) slightly improves
  3. Enemy-of-my-friend: if A and B are enemies, and C is A's friend,
     C's relationship with B gets a small negative adjustment

DramaCascade.on_resolved() is called from _apply_resolved in engine.py.
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine

DRAMA_VALENCE_THRESHOLD = -0.35   # interactions below this trigger drama
WITNESS_FRIENDSHIP_MIN  = 15.0    # minimum friendship to be "involved"
SIDES_FRIENDSHIP_MIN    = 35.0    # must know BOTH parties to take sides
ENEMY_THRESHOLD         = -30.0   # friendship below this = enemy
WITNESS_CREDIBILITY     = 1.5     # gossip credibility multiplier for witnesses


class DramaCascade:

    def on_resolved(
        self,
        sim_a: "Sim",
        sim_b: "Sim",
        valence: float,
        interaction: str,
        engine: "SimEngine",
    ) -> None:
        """Called after every resolved interaction. Fires drama logic if negative."""
        if valence >= DRAMA_VALENCE_THRESHOLD:
            # Positive interaction — friendly ripple (small boost to mutual friends)
            self._positive_ripple(sim_a, sim_b, valence, engine)
            return

        # Negative interaction — find witnesses and cascade drama
        witnesses = self._find_witnesses(sim_a, sim_b, engine)
        if witnesses:
            self._witness_gossip(sim_a, sim_b, interaction, valence, witnesses, engine)
            self._take_sides(sim_a, sim_b, valence, witnesses, engine)

        # Enemy-of-my-friend propagation
        self._enemy_propagation(sim_a, sim_b, engine)

    # ── Witness logic ─────────────────────────────────────────────────────────

    def _find_witnesses(
        self, sim_a: "Sim", sim_b: "Sim", engine: "SimEngine"
    ) -> list["Sim"]:
        """Sims that know at least one of the parties and are 'present'."""
        return [
            s for s in engine.sims
            if s.sim_id not in (sim_a.sim_id, sim_b.sim_id)
            and (
                engine.relationships.get(s.sim_id, sim_a.sim_id).friendship >= WITNESS_FRIENDSHIP_MIN
                or engine.relationships.get(s.sim_id, sim_b.sim_id).friendship >= WITNESS_FRIENDSHIP_MIN
            )
        ]

    def _witness_gossip(
        self,
        sim_a: "Sim",
        sim_b: "Sim",
        interaction: str,
        valence: float,
        witnesses: list["Sim"],
        engine: "SimEngine",
    ) -> None:
        """Witnesses gain credible gossip about the incident."""
        scandal_tag = (
            f"{sim_a.name} was witnessed being hostile toward {sim_b.name} "
            f"during '{interaction[:30]}'"
        )
        for witness in witnesses[:3]:   # cap at 3 witnesses for performance
            # Spread to two random sims (with credibility bonus)
            others = [s for s in engine.sims if s.sim_id != witness.sim_id]
            targets = random.sample(others, min(2, len(others)))
            for t in targets:
                engine.gossip.learn(witness.sim_id, sim_a.sim_id, scandal_tag)
                engine.gossip.spread(witness.sim_id, t.sim_id, sim_a.sim_id)

            witness.emotion.add("surprise", 0.4, duration=2, source="witnessed_drama")

    # ── Sides ─────────────────────────────────────────────────────────────────

    def _take_sides(
        self,
        sim_a: "Sim",
        sim_b: "Sim",
        valence: float,
        witnesses: list["Sim"],
        engine: "SimEngine",
    ) -> None:
        """
        Sims who know BOTH parties must take sides.
        The severity of the incident (valence) drives how strongly sides shift.
        """
        severity = abs(valence)
        for witness in witnesses:
            knows_a = engine.relationships.get(witness.sim_id, sim_a.sim_id).friendship
            knows_b = engine.relationships.get(witness.sim_id, sim_b.sim_id).friendship

            if knows_a < SIDES_FRIENDSHIP_MIN or knows_b < SIDES_FRIENDSHIP_MIN:
                continue

            # Witness sides with whoever they're closer to
            if knows_b >= knows_a:
                # Side with victim (B) — penalise aggressor (A)
                rel_with_a = engine.relationships.get(witness.sim_id, sim_a.sim_id)
                rel_with_a.apply_deltas(-severity * 4, 0)
                rel_with_b = engine.relationships.get(witness.sim_id, sim_b.sim_id)
                rel_with_b.apply_deltas(severity * 1.5, 0)
                witness.emotion.add("disapproval", 0.5, duration=3, source="took_sides")
            else:
                # Side with aggressor (A) — slight coolness toward victim
                rel_with_b = engine.relationships.get(witness.sim_id, sim_b.sim_id)
                rel_with_b.apply_deltas(-severity * 1.5, 0)

    # ── Enemy propagation ─────────────────────────────────────────────────────

    def _enemy_propagation(
        self, sim_a: "Sim", sim_b: "Sim", engine: "SimEngine"
    ) -> None:
        """
        If A and B become enemies (friendship < ENEMY_THRESHOLD),
        A's friends get a small negative adjustment toward B (and vice versa).
        """
        rel_ab = engine.relationships.get(sim_a.sim_id, sim_b.sim_id)
        if rel_ab.friendship >= ENEMY_THRESHOLD:
            return

        for sim in engine.sims:
            if sim.sim_id in (sim_a.sim_id, sim_b.sim_id):
                continue

            is_friend_of_a = engine.relationships.get(sim.sim_id, sim_a.sim_id).friendship >= 40
            is_friend_of_b = engine.relationships.get(sim.sim_id, sim_b.sim_id).friendship >= 40

            if is_friend_of_a and not is_friend_of_b:
                # Friend of A subtly dislikes B more
                rel = engine.relationships.get(sim.sim_id, sim_b.sim_id)
                rel.apply_deltas(-1.5, 0)

            if is_friend_of_b and not is_friend_of_a:
                # Friend of B subtly dislikes A more
                rel = engine.relationships.get(sim.sim_id, sim_a.sim_id)
                rel.apply_deltas(-1.5, 0)

    # ── Positive ripple ───────────────────────────────────────────────────────

    def _positive_ripple(
        self, sim_a: "Sim", sim_b: "Sim", valence: float, engine: "SimEngine"
    ) -> None:
        """
        Very positive interactions (valence > 0.8) create a small positive
        ripple for mutual friends — seeing a happy moment is uplifting.
        """
        if valence < 0.80:
            return
        for sim in engine.sims:
            if sim.sim_id in (sim_a.sim_id, sim_b.sim_id):
                continue
            knows_a = engine.relationships.get(sim.sim_id, sim_a.sim_id).friendship >= 40
            knows_b = engine.relationships.get(sim.sim_id, sim_b.sim_id).friendship >= 40
            if knows_a and knows_b:
                sim.emotion.add("joy", 0.15, duration=1, source="witnessed_happiness")
