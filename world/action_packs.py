from __future__ import annotations

import random


PACK_IDS = [
    "crime",
    "health",
    "parenting",
    "politics",
    "religion_cults",
    "reputation_media",
    "transportation",
    "housing_landlord",
    "education_deep",
    "crafting_industry",
    "pet_ecosystem",
    "disaster",
    "festival_season",
    "law_judicial",
    "espionage",
]


class ActionPackManager:
    """Single-phase bundle of emergent action packs.

    This manager activates all packs with lightweight mechanics that hook into
    existing systems (gossip, dynasties, weather, properties, careers, pets,
    relationships, economy) without requiring new heavy infrastructure.
    """

    def __init__(self) -> None:
        self.enabled: dict[str, bool] = {k: True for k in PACK_IDS}
        self.counters: dict[str, int] = {k: 0 for k in PACK_IDS}
        self.recent_events: list[dict] = []

    def enable(self, pack_id: str, enabled: bool) -> bool:
        if pack_id not in self.enabled:
            return False
        self.enabled[pack_id] = bool(enabled)
        return True

    def state(self) -> dict:
        return {
            "enabled": dict(self.enabled),
            "counters": dict(self.counters),
            "recent": list(self.recent_events[-60:]),
        }

    def tick(self, engine) -> None:
        for pack_id, on in self.enabled.items():
            if not on:
                continue
            fn = getattr(self, f"_tick_{pack_id}", None)
            if fn:
                fn(engine)

    def on_resolved(self, engine, sim_a, sim_b, result: dict, valence: float) -> None:
        if self.enabled.get("reputation_media", False) and abs(valence) > 0.55:
            self._emit(engine, "reputation_media", sim_a.sim_id, "viral_social_clip")
            sim_a.reputation_score = min(
                100.0, sim_a.reputation_score + (1.5 if valence > 0 else -1.0)
            )
        if self.enabled.get("crime", False) and valence < -0.65:
            self._emit(engine, "crime", sim_a.sim_id, "violent_incident")
            try:
                engine.gossip.learn(sim_b.sim_id, sim_a.sim_id, "public conflict")
            except Exception:
                pass
        if self.enabled.get("espionage", False) and random.random() < 0.03:
            self._emit(engine, "espionage", sim_a.sim_id, "secret_observed")
            sim_a.hacker_reputation = min(100.0, sim_a.hacker_reputation + 1.0)

    def _emit(self, engine, pack_id: str, sim_id: str, kind: str) -> None:
        self.counters[pack_id] = self.counters.get(pack_id, 0) + 1
        evt = {
            "tick": int(engine.tick_count),
            "pack": pack_id,
            "sim_id": sim_id,
            "kind": kind,
        }
        self.recent_events.append(evt)
        self.recent_events = self.recent_events[-300:]
        try:
            engine._bus.emit("action_pack_event", payload=evt, tick=engine.tick_count)
        except Exception:
            pass

    def _pick_sim(self, engine):
        return random.choice(engine.sims) if engine.sims else None

    def _tick_crime(self, engine) -> None:
        if random.random() > 0.02:
            return
        sim = self._pick_sim(engine)
        if not sim:
            return
        sim.reputation_score = max(-100.0, sim.reputation_score - 0.7)
        self._emit(engine, "crime", sim.sim_id, "petty_crime")

    def _tick_health(self, engine) -> None:
        if random.random() > 0.03:
            return
        sim = self._pick_sim(engine)
        if not sim:
            return
        sim.needs.energy = max(0.0, sim.needs.energy - 4.0)
        sim.emotion.add("fatigue", 0.25, duration=3, source="health_event")
        self._emit(engine, "health", sim.sim_id, "minor_illness")

    def _tick_parenting(self, engine) -> None:
        if random.random() > 0.03:
            return
        adults = [s for s in engine.sims if int(s.profile.get("age", 0)) >= 18]
        if not adults:
            return
        sim = random.choice(adults)
        sim.school_performance = min(100.0, sim.school_performance + 0.4)
        self._emit(engine, "parenting", sim.sim_id, "homework_support")

    def _tick_politics(self, engine) -> None:
        if random.random() > 0.015:
            return
        sim = self._pick_sim(engine)
        if not sim:
            return
        sim.reputation_score = min(100.0, sim.reputation_score + 0.6)
        self._emit(engine, "politics", sim.sim_id, "civic_campaign")

    def _tick_religion_cults(self, engine) -> None:
        if random.random() > 0.01:
            return
        sim = self._pick_sim(engine)
        if not sim:
            return
        sim.emotion.add("awe", 0.3, duration=3, source="ritual_gathering")
        self._emit(engine, "religion_cults", sim.sim_id, "ritual")

    def _tick_reputation_media(self, engine) -> None:
        if random.random() > 0.02:
            return
        sim = self._pick_sim(engine)
        if not sim:
            return
        sim.reputation_score = min(100.0, sim.reputation_score + 0.4)
        self._emit(engine, "reputation_media", sim.sim_id, "social_post")

    def _tick_transportation(self, engine) -> None:
        if random.random() > 0.02:
            return
        sim = self._pick_sim(engine)
        if not sim:
            return
        sim.needs.energy = max(0.0, sim.needs.energy - 2.0)
        self._emit(engine, "transportation", sim.sim_id, "commute_delay")

    def _tick_housing_landlord(self, engine) -> None:
        if random.random() > 0.02:
            return
        sim = self._pick_sim(engine)
        if not sim:
            return
        if sim.properties:
            _eng = getattr(sim, '_engine_ref', None)
            if _eng:
                from persistence.ledger import TX_ACTION_PACK_COST
                _eng._tx(sim, -20.0, TX_ACTION_PACK_COST, description='action pack cost')
            else:
                sim.simoleons = max(0.0, sim.simoleons - 20.0)
            self._emit(engine, "housing_landlord", sim.sim_id, "repair_cost")

    def _tick_education_deep(self, engine) -> None:
        if random.random() > 0.03:
            return
        sim = self._pick_sim(engine)
        if not sim:
            return
        sim.homework_progress = min(100.0, sim.homework_progress + 5.0)
        sim.school_performance = min(100.0, sim.school_performance + 0.5)
        self._emit(engine, "education_deep", sim.sim_id, "study_session")

    def _tick_crafting_industry(self, engine) -> None:
        if random.random() > 0.02:
            return
        sim = self._pick_sim(engine)
        if not sim:
            return
        _eng = getattr(sim, '_engine_ref', None)
        if _eng:
            from persistence.ledger import TX_ACTION_PACK_INCOME
            _eng._tx(sim, 10.0, TX_ACTION_PACK_INCOME, description='action pack reward')
        else:
            sim.simoleons += 10.0
        self._emit(engine, "crafting_industry", sim.sim_id, "small_sale")

    def _tick_pet_ecosystem(self, engine) -> None:
        if random.random() > 0.03:
            return
        pet_owners = [s for s in engine.sims if getattr(s, "pet_ids", [])]
        if not pet_owners:
            return
        sim = random.choice(pet_owners)
        sim.emotion.add("joy", 0.2, duration=2, source="pet_bond")
        self._emit(engine, "pet_ecosystem", sim.sim_id, "pet_companionship")

    def _tick_disaster(self, engine) -> None:
        if random.random() > 0.008:
            return
        sim = self._pick_sim(engine)
        if not sim:
            return
        sim.needs.energy = max(0.0, sim.needs.energy - 6.0)
        self._emit(engine, "disaster", sim.sim_id, "power_outage")

    def _tick_festival_season(self, engine) -> None:
        if random.random() > 0.02:
            return
        sim = self._pick_sim(engine)
        if not sim:
            return
        sim.needs.fun = min(100.0, sim.needs.fun + 4.0)
        self._emit(engine, "festival_season", sim.sim_id, "street_festival")

    def _tick_law_judicial(self, engine) -> None:
        if random.random() > 0.015:
            return
        sim = self._pick_sim(engine)
        if not sim:
            return
        _eng = getattr(sim, '_engine_ref', None)
        if _eng:
            from persistence.ledger import TX_ACTION_PACK_COST
            _eng._tx(sim, -12.0, TX_ACTION_PACK_COST, description='action pack cost')
        else:
            sim.simoleons = max(0.0, sim.simoleons - 12.0)
        self._emit(engine, "law_judicial", sim.sim_id, "fine_issued")

    def _tick_espionage(self, engine) -> None:
        if random.random() > 0.01:
            return
        sim = self._pick_sim(engine)
        if not sim:
            return
        sim.hacker_reputation = min(100.0, sim.hacker_reputation + 0.8)
        self._emit(engine, "espionage", sim.sim_id, "intel_trade")
