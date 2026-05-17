"""
world/phone.py — Async phone and text interactions between sims.

Sims can maintain relationships with others not currently at their venue
via phone calls and texts. These are lightweight heuristic interactions
(no LLM call needed) that apply small friendship/romance deltas.

PhoneSystem.tick() fires probabilistically each tick.
Max 1 phone action per sim per PHONE_COOLDOWN ticks.
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.engine import SimEngine

PHONE_COOLDOWN = 8         # ticks between phone actions per sim
PHONE_CHANCE   = 0.12      # per tick, per eligible sim


_PHONE_ACTIONS = [
    # (label, friendship_delta, romance_delta, social_restore, fun_restore, mood_boost)
    ("sent a funny meme",          1.5,  0.0,  4.0,  5.0, "amusement"),
    ("had a catch-up call",        2.5,  0.5,  8.0,  3.0, "joy"),
    ("sent a thinking-of-you text",2.0,  1.0,  5.0,  2.0, "caring"),
    ("voice note about their day", 1.8,  0.3,  6.0,  2.0, "optimism"),
    ("checked in after bad news",  3.0,  0.0,  7.0,  1.0, "gratitude"),
    ("sent a compliment",          2.0,  1.5,  4.0,  3.0, "admiration"),
    ("shared exciting news",       2.5,  0.5,  6.0,  4.0, "excitement"),
    ("invited to hang out later",  1.5,  0.5,  5.0,  3.0, "anticipating"),
]

# Actions only for sims with romance >= 30
_ROMANTIC_PHONE_ACTIONS = [
    ("sent a flirty text",          1.0,  3.0,  3.0,  4.0, "desire"),
    ("left a sweet voice note",     1.5,  2.5,  5.0,  3.0, "love"),
    ("planned a date over text",    2.0,  3.0,  6.0,  5.0, "anticipating"),
]


class PhoneSystem:

    def tick(self, engine: "SimEngine") -> None:
        tick = engine.tick_count
        sims = [s for s in engine.sims if not getattr(s, "_sleeping", False)]

        for sim in sims:
            last_phone = getattr(sim, "_last_phone_tick", -PHONE_COOLDOWN)
            if tick - last_phone < PHONE_COOLDOWN:
                continue
            if random.random() > PHONE_CHANCE:
                continue

            # Pick a target: close friend or partner not currently at same venue
            target = self._pick_target(sim, engine)
            if target is None:
                continue

            self._do_phone_action(sim, target, engine, tick)

    def _pick_target(self, sim, engine: "SimEngine"):
        candidates = [
            o for o in engine.sims
            if o.sim_id != sim.sim_id
            and engine.relationships.get(sim.sim_id, o.sim_id).friendship >= 25
        ]
        if not candidates:
            return None
        # Weight toward friends with higher friendship
        weights = [
            engine.relationships.get(sim.sim_id, o.sim_id).friendship
            for o in candidates
        ]
        return random.choices(candidates, weights=weights, k=1)[0]

    def _do_phone_action(self, sim, target, engine: "SimEngine", tick: int) -> None:
        rel = engine.relationships.get(sim.sim_id, target.sim_id)

        # Choose action type
        pool = list(_PHONE_ACTIONS)
        if rel.romance >= 30:
            pool += _ROMANTIC_PHONE_ACTIONS
        label, fd, rd, social_restore, fun_restore, mood = random.choice(pool)

        # Apply deltas
        rel.apply_deltas(fd, rd)
        sim.needs.restore("social", social_restore)
        sim.needs.restore("fun",    fun_restore)
        target.needs.restore("social", social_restore * 0.6)

        sim.emotion.add(mood, 0.4, duration=2, source="phone")
        sim._last_phone_tick = tick

        # Add a small memory
        memory_tag = f"phone: {sim.name} {label}"
        rel.add_memory(memory_tag, 0.4)

        engine._bus.emit(
            "phone_interaction",
            sim_a=sim,
            sim_b=target,
            action=label,
            friendship_delta=fd,
            romance_delta=rd,
            tick=tick,
        )

        import logging
        logging.getLogger(__name__).debug(
            "[Phone] %s → %s: %s  fd=%+.1f", sim.name, target.name, label, fd
        )
