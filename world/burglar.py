"""
world/burglar.py — Burglar NPC and burglary events.

Mechanics inspired by The Sims series:
  - Burglary checks mostly at night.
  - Higher chance when households are asleep and own more expensive objects.
  - Burglar targets highest-value placed lot objects first.
  - Burglar alarms improve chance of police catch and reduce losses.
  - Successful police response grants a small reward.
"""

from __future__ import annotations

import random


class BurglarSystem:
    def __init__(self) -> None:
        self.active: bool = False
        self.lot_id: str = ""
        self.name: str = "Robin Banks"
        self._cooldown_until_tick: int = 0
        self._event_log: list[dict] = []
        self._stolen_buffer: list[dict] = []
        self._last_threat_response: dict = {}

    def tick(self, engine, hour: int) -> None:
        if self.active:
            self._resolve_active(engine)
            return
        if engine.tick_count < self._cooldown_until_tick:
            return
        if hour < 22 and hour > 4:
            return
        self._maybe_start_burglary(engine, hour)

    def state(self) -> dict:
        return {
            "active": self.active,
            "lot_id": self.lot_id,
            "burglar_name": self.name,
            "cooldown_until_tick": self._cooldown_until_tick,
            "recent_events": list(self._event_log[-20:]),
            "last_threat_response": dict(self._last_threat_response),
        }

    def force_trigger(self, engine, lot_id: str | None = None) -> dict:
        if self.active:
            return {"ok": False, "reason": "already_active", "lot_id": self.lot_id}
        target_lot = lot_id or self._pick_lot(engine)
        if not target_lot:
            return {"ok": False, "reason": "no_lot_available"}
        self.active = True
        self.lot_id = target_lot
        self._stolen_buffer = []
        self._event_log.append(
            {
                "tick": engine.tick_count,
                "hour": None,
                "type": "burglary_forced",
                "lot_id": target_lot,
            }
        )
        return {"ok": True, "lot_id": target_lot}

    def _maybe_start_burglary(self, engine, hour: int) -> None:
        candidate_lots = self._occupied_residential_lots(engine)
        if not candidate_lots:
            return
        lot_id = ""
        sims_on_lot = []
        shuffled = list(candidate_lots)
        random.shuffle(shuffled)
        for lid in shuffled:
            occupants = [
                s for s in engine.sims if str(getattr(s, "current_lot_id", "")) == lid
            ]
            if occupants:
                lot_id = lid
                sims_on_lot = occupants
                break
        if not sims_on_lot:
            return

        all_asleep = all(bool(getattr(s, "_sleeping", False)) for s in sims_on_lot)
        base = 0.025 if not all_asleep else 0.055

        lot_wealth = self._lot_value(engine, lot_id)
        wealth_boost = min(0.08, lot_wealth / 150000.0)
        alarm = self._lot_has_alarm(engine, lot_id)
        alarm_malus = 0.015 if alarm else 0.0

        chance = max(0.005, min(0.22, base + wealth_boost - alarm_malus))
        if random.random() >= chance:
            return

        self.active = True
        self.lot_id = lot_id
        self._stolen_buffer = []
        event = {
            "tick": engine.tick_count,
            "hour": hour,
            "type": "burglary_started",
            "lot_id": lot_id,
            "chance": round(chance, 3),
            "lot_value": round(lot_wealth, 2),
            "alarm": alarm,
        }
        self._event_log.append(event)
        try:
            engine._bus.emit("burglary_started", **event)
        except Exception:
            pass

    def _resolve_active(self, engine) -> None:
        if not self.active:
            return
        lot_id = self.lot_id
        alarm = self._lot_has_alarm(engine, lot_id)
        intervention = self.resolve_dynamic_threat_response(
            engine, lot_id, threat_tag="burglary"
        )
        intervention_win = bool(intervention.get("success", False))
        police_base = 0.82 if alarm else 0.42
        if intervention_win:
            police_base = min(0.97, police_base + 0.35)
        police_win = random.random() < police_base

        if intervention_win:
            steals = random.randint(0, 1)
        else:
            steals = random.randint(1, 2 if alarm else 4)
        stolen = self._steal_items(engine, lot_id, steals)
        self._stolen_buffer.extend(stolen)

        if police_win:
            reimbursement = sum(float(i.get("market_price", 0.0)) for i in stolen)
            bonus = 500.0 if stolen else 250.0
            self._pay_household(engine, lot_id, reimbursement + bonus)
            outcome = "caught"
        else:
            outcome = "escaped"

        event = {
            "tick": engine.tick_count,
            "type": "burglary_resolved",
            "lot_id": lot_id,
            "outcome": outcome,
            "alarm": alarm,
            "intervention": intervention,
            "stolen_count": len(stolen),
            "stolen": stolen,
        }
        self._event_log.append(event)
        try:
            engine._bus.emit("burglary_resolved", **event)
        except Exception:
            pass
        try:
            if hasattr(engine, "_world_event_memory"):
                affected = [
                    s for s in engine.sims if getattr(s, "household_id", "") == lot_id
                ]
                engine._world_event_memory(
                    affected,
                    f"burglary_{outcome}",
                    valence=(-0.45 if outcome == "escaped" else 0.25),
                    gossip=True,
                )
        except Exception:
            pass

        for sim in engine.sims:
            if getattr(sim, "household_id", "") != lot_id:
                continue
            if outcome == "caught":
                sim.emotion.add("relief", 0.5, duration=6, source="burglar_caught")
            else:
                sim.emotion.add("stress", 0.5, duration=6, source="burglar_escaped")
            if hasattr(sim, "moodlets"):
                sim.moodlets.add("stressed", source=f"burglar_{outcome}")

        self.active = False
        self.lot_id = ""
        self._stolen_buffer = []
        self._cooldown_until_tick = engine.tick_count + 36

    def resolve_dynamic_threat_response(
        self, engine, lot_id: str, threat_tag: str
    ) -> dict:
        """
        Emergent response model:
        - Pick likely defender from sims on lot.
        - Estimate defense power from traits + inventory + room items.
        - Convert power to success chance for current threat.

        This is generic and can be reused by other threat events.
        """
        sims_on_lot = [
            s
            for s in engine.sims
            if str(getattr(s, "current_lot_id", "")) == lot_id
            and not getattr(s, "is_ghost", False)
        ]
        if not sims_on_lot:
            return {"used": False, "reason": "no_sims"}

        defender, score = self._pick_defender(sims_on_lot, lot_id, engine)
        if defender is None or score <= 0:
            return {"used": False, "reason": "no_defender"}

        # Threat tuning by tag; burglary is moderately difficult.
        difficulty = {
            "burglary": 1.0,
            "fire": 1.15,
            "hostile_npc": 1.2,
        }.get(str(threat_tag), 1.0)
        chance = max(0.08, min(0.92, 0.18 + (score / 10.0) * (0.55 / difficulty)))
        success = random.random() < chance

        if success:
            defender.emotion.add(
                "confidence", 0.6, duration=5, source=f"defense:{threat_tag}"
            )
            if hasattr(defender, "moodlets"):
                defender.moodlets.add("on_a_roll", source=f"defense:{threat_tag}")
        else:
            defender.emotion.add(
                "nervousness", 0.5, duration=4, source=f"defense_failed:{threat_tag}"
            )

        result = {
            "used": True,
            "success": success,
            "chance": round(chance, 3),
            "defender_id": defender.sim_id,
            "defender_name": defender.name,
            "score": round(score, 2),
            "threat_tag": str(threat_tag),
            "lot_id": str(lot_id),
        }
        self._last_threat_response = dict(result)
        return result

    def _pick_defender(
        self, sims_on_lot: list, lot_id: str, engine
    ) -> tuple[object | None, float]:
        best = None
        best_score = -1.0
        for sim in sims_on_lot:
            score = self._defense_score(sim, lot_id, engine)
            if score > best_score:
                best_score = score
                best = sim
        return best, best_score

    def _defense_score(self, sim, lot_id: str, engine) -> float:
        traits = set(str(t).lower() for t in sim.profile.get("traits", []))
        inv = list(getattr(sim, "inventory_objects", []))
        lot = engine.lot_layout._placements.get(lot_id, {})

        score = 0.0
        if not getattr(sim, "_sleeping", False):
            score += 0.9
        # Personality-driven bravery, not hardcoded outcomes.
        if {"brave", "bold", "hot-headed", "active"} & traits:
            score += 1.4
        if {"coward", "lazy", "gloomy"} & traits:
            score -= 0.7

        # Skill confidence contributes to emergent defense behavior.
        score += float(sim.skills.levels.get("fitness", 0)) * 0.18
        score += float(sim.skills.levels.get("logic", 0)) * 0.08

        # Defensive capability from inventory items.
        for item in inv:
            itype = str(item.get("type", "")).lower()
            name = str(item.get("name", "")).lower()
            if itype in {"weapon", "armor", "explosive"}:
                score += 0.7
            if any(k in name for k in ["alarm", "spray", "shield", "baton", "stun"]):
                score += 0.5

        # Room-level defensive installations contribute globally.
        for items in lot.values():
            for item in items:
                name = str(item.get("name", "")).lower()
                subtype = str(item.get("sub_type", "")).lower()
                if "alarm" in name or "alarm" in subtype:
                    score += 0.5
                if any(k in name for k in ["camera", "turret", "sentry", "security"]):
                    score += 0.6
        return max(0.0, score)

    def _pick_lot(self, engine) -> str:
        candidate_lots = self._occupied_residential_lots(engine)
        return random.choice(candidate_lots) if candidate_lots else ""

    def _occupied_residential_lots(self, engine) -> list[str]:
        try:
            mapping = getattr(engine.neighborhoods, "household_lot_map", {})
            lots = sorted({str(v) for v in mapping.values() if v})
            if lots:
                return lots
        except Exception:
            pass
        return [hh.id for hh in getattr(engine, "households", []) if hh.member_ids]

    def _steal_items(self, engine, lot_id: str, max_items: int) -> list[dict]:
        layout = engine.lot_layout._placements.get(lot_id, {})
        pool: list[tuple[str, dict]] = []
        for zone, items in layout.items():
            for item in items:
                pool.append((zone, item))
        pool.sort(key=lambda z: float(z[1].get("market_price", 0.0)), reverse=True)
        stolen: list[dict] = []
        for zone, item in pool[: max(0, max_items)]:
            removed = engine.lot_layout.remove(lot_id, int(item.get("id", -1)))
            if removed.get("ok"):
                stolen.append({"zone": zone, **dict(removed.get("item", {}))})
        return stolen

    def _lot_value(self, engine, lot_id: str) -> float:
        total = 0.0
        lot = engine.lot_layout._placements.get(lot_id, {})
        for items in lot.values():
            for item in items:
                total += float(item.get("market_price", 0.0))
        return total

    def _lot_has_alarm(self, engine, lot_id: str) -> bool:
        lot = engine.lot_layout._placements.get(lot_id, {})
        for items in lot.values():
            for item in items:
                name = str(item.get("name", "")).lower()
                sub_type = str(item.get("sub_type", "")).lower()
                if "alarm" in name or "alarm" in sub_type:
                    return True
        # Also consider portable defenses still in occupant inventories
        for sim in engine.sims:
            cur = str(getattr(sim, "current_lot_id", ""))
            if cur != lot_id:
                continue
            for item in getattr(sim, "inventory_objects", []):
                n = str(item.get("name", "")).lower()
                t = str(item.get("type", "")).lower()
                if "alarm" in n or "security" in n or t in {"weapon", "armor", "tool"}:
                    return True
        return False

    def _pay_household(self, engine, lot_id: str, amount: float) -> None:
        if amount <= 0:
            return
        household = next((h for h in engine.households if h.id == lot_id), None)
        if household is not None:
            household.funds += float(amount)
            return
        members = [s for s in engine.sims if getattr(s, "household_id", "") == lot_id]
        if not members:
            return
        split = float(amount) / float(len(members))
        for sim in members:
            _eng = getattr(sim, '_engine_ref', None)
            if _eng:
                from persistence.ledger import TX_BURGLAR_TAKE
                _eng._tx(sim, split, TX_BURGLAR_TAKE, description='burglary proceeds')
            else:
                sim.simoleons += split
