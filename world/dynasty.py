from __future__ import annotations

import random
from dataclasses import dataclass

from core.dynasty import Dynasty, DynastyPerk, IDEAL_CONFLICTS


@dataclass(frozen=True)
class PerkDef:
    category: str
    cost: int
    required_prestige: int


PERK_DEFS: dict[str, PerkDef] = {
    "grade_boost": PerkDef("success", 25, 1),
    "nepotism": PerkDef("success", 50, 1),
    "market_manipulator": PerkDef("success", 50, 2),
    "respected_head": PerkDef("social", 25, 1),
    "discreet": PerkDef("social", 50, 1),
    "friends_in_high_places": PerkDef("social", 50, 2),
    "creative_affinity": PerkDef("inheritable", 150, 3),
    "social_affinity": PerkDef("inheritable", 150, 3),
    "mental_affinity": PerkDef("inheritable", 150, 3),
    "physical_affinity": PerkDef("inheritable", 150, 3),
}


class DynastyManager:
    def __init__(self) -> None:
        self.dynasties: dict[str, Dynasty] = {}

    def create_dynasty(
        self,
        creator_id: str,
        name: str,
        description: str = "",
        crest: dict | None = None,
        member_ids: list[str] | None = None,
        ideals: list[str] | None = None,
        focus_skills: list[str] | None = None,
    ) -> Dynasty:
        did = f"dyn_{creator_id[:6]}_{len(self.dynasties) + 1}"
        members = list(dict.fromkeys(([creator_id] + list(member_ids or []))))
        d = Dynasty(
            dynasty_id=did,
            name=name.strip() or f"{creator_id[:6]} Dynasty",
            description=description.strip(),
            crest=dict(crest or {}),
            head_id=creator_id,
            heir_id=creator_id,
            member_ids=members,
            ideals=self._sanitize_ideals(ideals or []),
            focus_skills=list(dict.fromkeys((focus_skills or [])[:3])),
        )
        self.dynasties[did] = d
        return d

    def assign_sim(self, sim, dynasty_id: str | None) -> None:
        sim.dynasty_id = dynasty_id

    def set_heir(self, dynasty_id: str, heir_id: str) -> bool:
        d = self.dynasties.get(dynasty_id)
        if not d or heir_id not in d.member_ids:
            return False
        d.heir_id = heir_id
        return True

    def mark_outcast(self, dynasty_id: str, sim_id: str) -> bool:
        d = self.dynasties.get(dynasty_id)
        if not d or sim_id not in d.member_ids:
            return False
        d.member_ids = [x for x in d.member_ids if x != sim_id]
        if sim_id not in d.outcast_ids:
            d.outcast_ids.append(sim_id)
        if d.heir_id == sim_id:
            d.heir_id = d.head_id
        return True

    def add_alliance(self, dynasty_id: str, other_dynasty_id: str) -> bool:
        d = self.dynasties.get(dynasty_id)
        o = self.dynasties.get(other_dynasty_id)
        if not d or not o or dynasty_id == other_dynasty_id:
            return False
        d.alliances.add(other_dynasty_id)
        o.alliances.add(dynasty_id)
        return True

    def add_rivalry(self, dynasty_id: str, other_dynasty_id: str) -> bool:
        d = self.dynasties.get(dynasty_id)
        o = self.dynasties.get(other_dynasty_id)
        if not d or not o or dynasty_id == other_dynasty_id:
            return False
        d.rivalries.add(other_dynasty_id)
        o.rivalries.add(dynasty_id)
        return True

    def spend_perk_points(self, dynasty_id: str, perk_id: str) -> bool:
        d = self.dynasties.get(dynasty_id)
        p = PERK_DEFS.get(perk_id)
        if not d or not p or d.prestige_level < p.required_prestige:
            return False
        if d.perk_points < p.cost:
            return False
        d.perk_points -= p.cost
        cur = d.perks.get(perk_id)
        if not cur:
            d.perks[perk_id] = DynastyPerk(perk_id=perk_id, level=1)
        else:
            cur.level += 1
        return True

    def on_interaction(
        self, engine, sim_a, sim_b, interaction: str, valence: float
    ) -> None:
        da = self.dynasties.get(getattr(sim_a, "dynasty_id", None) or "")
        db = self.dynasties.get(getattr(sim_b, "dynasty_id", None) or "")
        witness_factor = self._witness_factor(engine, sim_a, sim_b)
        if da:
            self._score_interaction(
                da,
                sim_a,
                interaction,
                valence,
                is_actor=True,
                witness_factor=witness_factor,
            )
        if db:
            self._score_interaction(
                db,
                sim_b,
                interaction,
                valence,
                is_actor=False,
                witness_factor=witness_factor,
            )

    def on_trade(
        self, seller, buyer, total_price: float, item: dict | None = None
    ) -> None:
        d = self.dynasties.get(getattr(seller, "dynasty_id", None) or "")
        if d:
            self._add_prestige(d, min(2.0, total_price / 450.0))
            if "hardworking" in d.ideals:
                self._add_prestige(d, min(1.0, total_price / 900.0))
            if (
                "connoisseur" in d.ideals
                and item
                and str(item.get("rarity", ""))
                in {
                    "rare",
                    "epic",
                    "legendary",
                }
            ):
                self._add_prestige(d, 0.7)
        db = self.dynasties.get(getattr(buyer, "dynasty_id", None) or "")
        if db:
            self._add_unity(db, 0.1)
            if (
                "connoisseur" in db.ideals
                and item
                and float(item.get("market_price", 0.0)) > 1500
            ):
                self._add_prestige(db, 0.4)

    def on_gift(self, giver, receiver) -> None:
        dg = self.dynasties.get(getattr(giver, "dynasty_id", None) or "")
        dr = self.dynasties.get(getattr(receiver, "dynasty_id", None) or "")
        if dg:
            self._add_prestige(dg, 0.8)
            self._add_unity(dg, 0.35)
        if dr:
            self._add_unity(dr, 0.2)

    def on_item_use(self, sim, effect: dict) -> None:
        d = self.dynasties.get(getattr(sim, "dynasty_id", None) or "")
        if not d:
            return
        mood = str(effect.get("emotion", ""))
        need = str(effect.get("need", ""))
        if mood in {"joy", "satisfaction", "relief"}:
            self._add_unity(d, 0.2)
        if "nature-loving" in d.ideals and need in {"comfort"}:
            self._add_prestige(d, 0.2)
        if "jolly" in d.ideals and need in {"fun"}:
            self._add_prestige(d, 0.25)
        if mood in {"euphoria"}:
            self._add_scandal(d, "reckless_consumption", severity=1, witness_factor=1.0)

    def on_child_born(self, child, parent_a, parent_b) -> None:
        da = self.dynasties.get(getattr(parent_a, "dynasty_id", None) or "")
        db = self.dynasties.get(getattr(parent_b, "dynasty_id", None) or "")
        target = da or db
        if not target:
            return
        child.dynasty_id = target.dynasty_id
        if child.sim_id not in target.member_ids:
            target.member_ids.append(child.sim_id)
        self._add_prestige(target, 4.0)
        self._add_unity(target, 1.5)
        self._apply_inheritable_traits(child, target)

    def tick(self, engine) -> None:
        for d in self.dynasties.values():
            member_count = max(1, len(d.member_ids))
            decay = 0.015 + member_count * 0.004
            d.unity = max(0.0, min(100.0, d.unity - decay))
            if d.unity > 65.0:
                self._add_prestige(d, 0.08)
            if d.unity < 20.0 and random.random() < 0.08 and d.member_ids:
                candidate = random.choice(d.member_ids)
                if candidate != d.head_id:
                    d.heir_id = candidate
            if d.unity < 12.0 and random.random() < 0.04:
                self._add_scandal(d, "unity_crisis", severity=2, witness_factor=1.0)
            d.scandals = [
                s for s in d.scandals if int(s.get("ttl", 0)) > engine.tick_count
            ]

    def state(self) -> list[dict]:
        return [d.state() for d in self.dynasties.values()]

    def _sanitize_ideals(self, ideals: list[str]) -> list[str]:
        clean: list[str] = []
        for raw in ideals[:3]:
            ideal = str(raw).strip().lower()
            if not ideal:
                continue
            conflict = IDEAL_CONFLICTS.get(ideal, set())
            if any(existing in conflict for existing in clean):
                continue
            clean.append(ideal)
        return clean

    def _score_interaction(
        self,
        d: Dynasty,
        sim,
        interaction: str,
        valence: float,
        is_actor: bool,
        witness_factor: float,
    ) -> None:
        text = interaction.lower()
        if valence > 0.35:
            self._add_prestige(d, 0.45 if is_actor else 0.2)
            self._add_unity(d, 0.14)
        elif valence < -0.35:
            self._add_unity(d, -0.28)
            if any(k in text for k in ["cheat", "betray", "hack", "fight", "insult"]):
                severity = 1
                if valence < -0.7:
                    severity += 1
                self._add_scandal(
                    d,
                    "public_conflict",
                    severity=severity,
                    witness_factor=witness_factor,
                )

        career = str(getattr(sim, "career_id", "") or "").lower()
        if "hardworking" in d.ideals and career in {
            "business",
            "law",
            "doctor",
            "engineer",
            "politician",
        }:
            self._add_prestige(d, 0.12)
        if "scholarly" in d.ideals and career in {
            "scientist",
            "educator",
            "doctor",
            "engineer",
        }:
            self._add_prestige(d, 0.15)
        if "artistic" in d.ideals and career in {
            "painter",
            "musician",
            "writer",
            "actor",
            "style_influencer",
        }:
            self._add_prestige(d, 0.14)

        ideals = set(d.ideals)
        if "caring" in ideals and any(
            k in text for k in ["support", "comfort", "reassure", "gift"]
        ):
            self._add_prestige(d, 0.65)
        if "devious" in ideals and any(
            k in text for k in ["hack", "mischief", "sabotage"]
        ):
            self._add_prestige(d, 0.5)
        if "hardworking" in ideals and any(
            k in text for k in ["mentor", "advice", "work"]
        ):
            self._add_prestige(d, 0.4)
        if "jolly" in ideals and any(
            k in text for k in ["joke", "funny", "dance", "play"]
        ):
            self._add_prestige(d, 0.42)
        if "diplomatic" in ideals and any(
            k in text
            for k in ["chat", "compliment", "advice", "confide", "speech", "debate"]
        ):
            self._add_prestige(d, 0.45)
            self._add_unity(d, 0.15)
        if "passionate" in ideals and any(
            k in text for k in ["flirt", "romantic", "date", "kiss", "hold hands"]
        ):
            self._add_prestige(d, 0.4)
        if "mysterious" in ideals and any(
            k in text for k in ["secret", "occult", "mystery", "seance", "stargaz"]
        ):
            self._add_prestige(d, 0.35)
        if "nature-loving" in ideals and any(
            k in text for k in ["garden", "fish", "pet", "nature", "outdoor"]
        ):
            self._add_prestige(d, 0.35)
        if "bold" in ideals and any(
            k in text for k in ["duel", "challenge", "adventure", "climb", "rocket"]
        ):
            self._add_prestige(d, 0.35)
        if "vicious" in ideals and any(
            k in text for k in ["fight", "duel", "argue", "mock"]
        ):
            self._add_prestige(d, 0.3)

    def _add_prestige(self, d: Dynasty, amount: float) -> None:
        d.prestige_points = max(0.0, d.prestige_points + amount)
        target_level = int(min(10, 1 + d.prestige_points // 25.0))
        if target_level > d.prestige_level:
            gained = target_level - d.prestige_level
            d.prestige_level = target_level
            d.perk_points += gained * 25

    def _add_unity(self, d: Dynasty, amount: float) -> None:
        d.unity = max(0.0, min(100.0, d.unity + amount))

    def _add_scandal(
        self, d: Dynasty, kind: str, severity: int, witness_factor: float = 1.0
    ) -> None:
        sev = max(1, int(round(float(severity) * max(0.7, witness_factor))))
        d.scandals.append(
            {
                "kind": kind,
                "severity": sev,
                "ttl": 10_000,
            }
        )
        self._add_prestige(d, -float(sev) * 1.25)
        self._add_unity(d, -float(sev) * 0.9)

    def _witness_factor(self, engine, sim_a, sim_b) -> float:
        try:
            witnesses = 0
            for other in engine.sims:
                if other.sim_id in {sim_a.sim_id, sim_b.sim_id}:
                    continue
                rel_a = engine.relationships.get(sim_a.sim_id, other.sim_id)
                rel_b = engine.relationships.get(sim_b.sim_id, other.sim_id)
                if max(rel_a.friendship, rel_b.friendship) >= 35:
                    witnesses += 1
            return max(0.8, min(2.2, 1.0 + witnesses * 0.12))
        except Exception:
            return 1.0

    def _apply_inheritable_traits(self, child, dynasty: Dynasty) -> None:
        trait_map = {
            "creative_affinity": "creative_affinity_l1",
            "social_affinity": "social_affinity_l1",
            "mental_affinity": "mental_affinity_l1",
            "physical_affinity": "physical_affinity_l1",
        }
        for perk_id, trait in trait_map.items():
            if perk_id not in dynasty.perks:
                continue
            if trait not in child.profile.get("traits", []):
                child.profile.setdefault("traits", []).append(trait)
